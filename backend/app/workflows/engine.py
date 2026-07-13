"""Asynchronous workflow engine.

Executes :class:`WorkflowTask` rows from PostgreSQL with priority ordering,
dependency management, scheduling, bounded parallelism, cancellation, retry
with exponential backoff, crash recovery, and realtime progress events.

Claiming uses ``FOR UPDATE SKIP LOCKED`` so the design stays correct if the
platform later runs multiple workers.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import db
from app.core.realtime import manager
from app.models.task import TaskStatus, WorkflowTask
from app.models.user import User

logger = logging.getLogger("jarvis.workflow")


@dataclass
class TaskRunContext:
    db: AsyncSession
    task: WorkflowTask
    user: User
    _engine: WorkflowEngine

    async def report(self, progress: float, note: str | None = None) -> None:
        """Report progress (0..1) from inside a handler."""
        self.task.progress = max(0.0, min(1.0, progress))
        if note:
            self.task.progress_note = note[:500]
        await self.db.flush()
        await self._engine.publish(self.user.id, self.task)


TaskHandler = Callable[[TaskRunContext], Awaitable[dict[str, Any] | None]]


class WorkflowEngine:
    def __init__(self) -> None:
        settings = get_settings()
        self.handlers: dict[str, TaskHandler] = {}
        self.max_parallel = settings.workflow_max_parallel
        self.poll_interval = settings.workflow_poll_interval_seconds
        self.default_max_retries = settings.workflow_default_max_retries
        self.retry_base_delay = 5.0
        self._running_tasks: dict[uuid.UUID, asyncio.Task] = {}
        # Created fresh in start(): asyncio.Event binds to whichever loop
        # first awaits it, so a stale instance from a prior start()/stop()
        # cycle on a different event loop must never be reused.
        self._wake: asyncio.Event | None = None
        self._scheduler: asyncio.Task | None = None
        self._stopping = False

    # --- Registration -------------------------------------------------------

    def register_handler(self, name: str, handler: TaskHandler) -> None:
        self.handlers[name] = handler

    # --- Lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        if self._scheduler is not None:
            return
        self._stopping = False
        self._wake = asyncio.Event()
        await self._recover_orphans()
        self._scheduler = asyncio.create_task(self._run_scheduler(), name="workflow-scheduler")
        logger.info(
            "Workflow engine started (max_parallel=%s, handlers=%s)",
            self.max_parallel,
            sorted(self.handlers),
        )

    async def stop(self) -> None:
        self._stopping = True
        if self._wake is not None:
            self._wake.set()
        if self._scheduler is not None:
            self._scheduler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._scheduler
            self._scheduler = None
        for running in list(self._running_tasks.values()):
            running.cancel()
        for running in list(self._running_tasks.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await running
        self._running_tasks.clear()
        self._wake = None
        logger.info("Workflow engine stopped")

    def wake(self) -> None:
        """Nudge the scheduler (e.g. after a task was enqueued)."""
        if self._wake is not None:
            self._wake.set()

    def request_cancel(self, task_id: uuid.UUID) -> bool:
        running = self._running_tasks.get(task_id)
        if running is not None:
            running.cancel()
            return True
        return False

    @property
    def running_count(self) -> int:
        return len(self._running_tasks)

    # --- Scheduler ------------------------------------------------------------

    async def _run_scheduler(self) -> None:
        # Bound to this loop for the task's lifetime; start() always creates
        # a fresh Event immediately before spawning this task.
        wake = self._wake
        assert wake is not None
        while not self._stopping:
            try:
                claimed = await self._claim_due_tasks()
                for task_id in claimed:
                    runner = asyncio.create_task(
                        self._execute(task_id), name=f"workflow-task-{task_id}"
                    )
                    self._running_tasks[task_id] = runner
                    runner.add_done_callback(
                        lambda t, tid=task_id: self._running_tasks.pop(tid, None)
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Workflow scheduler iteration failed")

            wake.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=self.poll_interval)

    async def _claim_due_tasks(self) -> list[uuid.UUID]:
        slots = self.max_parallel - len(self._running_tasks)
        if slots <= 0:
            return []
        claimed: list[uuid.UUID] = []
        now = datetime.now(UTC)
        async with db.sessionmaker() as session:
            result = await session.execute(
                select(WorkflowTask)
                .where(
                    WorkflowTask.status == TaskStatus.PENDING,
                    or_(
                        WorkflowTask.scheduled_for.is_(None),
                        WorkflowTask.scheduled_for <= now,
                    ),
                )
                .order_by(WorkflowTask.priority.desc(), WorkflowTask.created_at.asc())
                .limit(slots * 3)
                .with_for_update(skip_locked=True)
            )
            for task in result.scalars().all():
                if len(claimed) >= slots:
                    break
                ready, dep_failure = await self._dependencies_state(session, task)
                if dep_failure:
                    task.status = TaskStatus.FAILED
                    task.error = f"Dependency failed: {dep_failure}"
                    task.finished_at = now
                    await self.publish(task.user_id, task)
                    continue
                if not ready:
                    continue
                task.status = TaskStatus.RUNNING
                task.attempts += 1
                task.started_at = now
                task.error = None
                claimed.append(task.id)
            await session.commit()
        return claimed

    @staticmethod
    async def _dependencies_state(
        session: AsyncSession, task: WorkflowTask
    ) -> tuple[bool, str | None]:
        """Returns (all_completed, failed_dependency_title_or_None)."""
        if not task.depends_on:
            return True, None
        result = await session.execute(
            select(WorkflowTask).where(WorkflowTask.id.in_(task.depends_on))
        )
        deps = list(result.scalars().all())
        for dep in deps:
            if dep.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
                return False, dep.title
        if len(deps) < len(set(task.depends_on)):
            return False, "missing dependency"
        return all(dep.status == TaskStatus.COMPLETED for dep in deps), None

    # --- Execution --------------------------------------------------------------

    async def _execute(self, task_id: uuid.UUID) -> None:
        async with db.sessionmaker() as session:
            task = await session.get(WorkflowTask, task_id)
            if task is None:
                return
            user = await session.get(User, task.user_id)
            if user is None:
                task.status = TaskStatus.FAILED
                task.error = "Owner no longer exists"
                task.finished_at = datetime.now(UTC)
                await session.commit()
                return

            handler = self.handlers.get(task.handler)
            await self.publish(user.id, task)
            try:
                if handler is None:
                    raise RuntimeError(f"No handler registered for '{task.handler}'")
                ctx = TaskRunContext(db=session, task=task, user=user, _engine=self)
                result = await handler(ctx)
                task.status = TaskStatus.COMPLETED
                task.progress = 1.0
                task.result = result
                task.finished_at = datetime.now(UTC)
            except asyncio.CancelledError:
                # Cooperative cancellation: reflect it unless the row was
                # already finalized by TaskService.cancel in another session.
                await session.rollback()
                fresh = await session.get(WorkflowTask, task_id)
                if fresh is not None and fresh.status == TaskStatus.RUNNING:
                    fresh.status = TaskStatus.CANCELLED
                    fresh.error = "Cancelled"
                    fresh.finished_at = datetime.now(UTC)
                    await session.commit()
                    await self.publish(fresh.user_id, fresh)
                if self._stopping:
                    raise
                return
            except Exception as exc:
                await session.rollback()
                task = await session.get(WorkflowTask, task_id)
                if task is None:
                    return
                logger.exception("Task %s (%s) failed", task_id, task.handler)
                if task.attempts <= task.max_retries:
                    # Retry with exponential backoff.
                    delay = min(300, self.retry_base_delay * (2 ** (task.attempts - 1)))
                    task.status = TaskStatus.PENDING
                    task.scheduled_for = datetime.now(UTC) + timedelta(seconds=delay)
                    task.error = f"Attempt {task.attempts} failed: {exc}"
                else:
                    task.status = TaskStatus.FAILED
                    task.error = str(exc)[:2000]
                    task.finished_at = datetime.now(UTC)

            await session.commit()
            await self.publish(task.user_id, task)

    async def _recover_orphans(self) -> None:
        """Requeue tasks stuck RUNNING from a previous process crash."""
        async with db.sessionmaker() as session:
            result = await session.execute(
                select(WorkflowTask).where(WorkflowTask.status == TaskStatus.RUNNING)
            )
            orphans = list(result.scalars().all())
            for task in orphans:
                task.status = TaskStatus.PENDING
                task.error = "Recovered after engine restart"
            await session.commit()
        if orphans:
            logger.warning("Recovered %s orphaned running task(s)", len(orphans))

    # --- Events -----------------------------------------------------------------

    async def publish(self, user_id: uuid.UUID, task: WorkflowTask) -> None:
        await manager.send_to_user(
            user_id,
            {
                "type": "task.update",
                "task": {
                    "id": str(task.id),
                    "title": task.title,
                    "status": task.status.value,
                    "progress": task.progress,
                    "progress_note": task.progress_note,
                    "error": task.error,
                },
            },
        )


_engine: WorkflowEngine | None = None


def get_workflow_engine() -> WorkflowEngine:
    global _engine
    if _engine is None:
        _engine = WorkflowEngine()
    return _engine
