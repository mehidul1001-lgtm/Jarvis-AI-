"""Workflow task queue: creation, querying, cancellation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationFailedError
from app.models.task import TaskPriority, TaskStatus, WorkflowTask
from app.models.user import User
from app.services.audit_service import AuditService


class TaskService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.audit = AuditService(db)

    async def create(
        self,
        user: User,
        *,
        title: str,
        handler: str,
        description: str | None = None,
        payload: dict | None = None,
        priority: int = TaskPriority.NORMAL.value,
        depends_on: list[uuid.UUID] | None = None,
        max_retries: int | None = None,
        scheduled_for: datetime | None = None,
    ) -> WorkflowTask:
        from app.workflows.engine import get_workflow_engine  # avoid circular import

        engine = get_workflow_engine()
        if handler not in engine.handlers:
            raise ValidationFailedError(f"Unknown task handler '{handler}'")
        if depends_on:
            found = (
                await self.db.execute(
                    select(func.count())
                    .select_from(WorkflowTask)
                    .where(WorkflowTask.id.in_(depends_on), WorkflowTask.user_id == user.id)
                )
            ).scalar_one()
            if found != len(set(depends_on)):
                raise ValidationFailedError("One or more dependency tasks do not exist")

        task = WorkflowTask(
            user_id=user.id,
            title=title.strip()[:300],
            description=description,
            handler=handler,
            payload=payload,
            priority=max(0, min(10, priority)),
            depends_on=depends_on,
            max_retries=(max_retries if max_retries is not None else engine.default_max_retries),
            scheduled_for=scheduled_for,
        )
        self.db.add(task)
        await self.db.flush()
        await self.audit.record(
            "task.created",
            user_id=user.id,
            resource=f"task:{task.id}",
            detail={"handler": handler, "title": task.title, "priority": task.priority},
        )
        engine.wake()
        return task

    async def get(self, user: User, task_id: uuid.UUID) -> WorkflowTask:
        task = await self.db.get(WorkflowTask, task_id)
        if task is None or task.user_id != user.id:
            raise NotFoundError("Task not found")
        return task

    async def list(
        self,
        user: User,
        *,
        status: TaskStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[WorkflowTask], int]:
        query = select(WorkflowTask).where(WorkflowTask.user_id == user.id)
        if status is not None:
            query = query.where(WorkflowTask.status == status)
        total = (
            await self.db.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        result = await self.db.execute(
            query.order_by(WorkflowTask.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def cancel(self, user: User, task_id: uuid.UUID) -> WorkflowTask:
        from app.workflows.engine import get_workflow_engine

        task = await self.get(user, task_id)
        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            raise ValidationFailedError(f"Task is already {task.status.value}")

        engine = get_workflow_engine()
        if task.status == TaskStatus.RUNNING:
            engine.request_cancel(task.id)
        task.status = TaskStatus.CANCELLED
        task.finished_at = datetime.now(UTC)
        task.error = "Cancelled by user"
        await self.db.flush()
        await self.audit.record("task.cancelled", user_id=user.id, resource=f"task:{task.id}")
        return task

    async def counts_by_status(self, user: User) -> dict[str, int]:
        result = await self.db.execute(
            select(WorkflowTask.status, func.count())
            .where(WorkflowTask.user_id == user.id)
            .group_by(WorkflowTask.status)
        )
        counts = {status.value: 0 for status in TaskStatus}
        for status, count in result.all():
            counts[status.value] = count
        return counts
