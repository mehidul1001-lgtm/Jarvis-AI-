"""Workflow engine tests: execution, deps, retry, cancellation, progress."""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.database import db
from app.models.task import TaskStatus
from app.models.user import User
from app.services.task_service import TaskService
from app.workflows.engine import get_workflow_engine
from tests.conftest import auth_header


async def _admin() -> User:
    async with db.sessionmaker() as session:
        return (
            await session.execute(select(User).where(User.email == "admin@example.com"))
        ).scalar_one()


@pytest.fixture
async def engine(client):  # depends on client so db points at the test database
    engine = get_workflow_engine()
    engine.retry_base_delay = 0.05
    await engine.start()
    yield engine
    await engine.stop()


async def _wait_status(task_id, statuses, timeout=8.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with db.sessionmaker() as session:
            from app.models.task import WorkflowTask

            task = await session.get(WorkflowTask, task_id)
            if task and task.status in statuses:
                return task
        await asyncio.sleep(0.05)
    raise AssertionError(f"task never reached {statuses}")


async def test_task_executes_with_progress_and_result(client, admin_tokens, engine):
    ran = []

    async def handler(ctx):
        await ctx.report(0.5, "halfway")
        ran.append(ctx.task.id)
        return {"answer": 42}

    engine.register_handler("test.echo", handler)
    user = await _admin()
    async with db.sessionmaker() as session:
        task = await TaskService(session).create(user, title="echo", handler="test.echo")
        await session.commit()

    done = await _wait_status(task.id, {TaskStatus.COMPLETED})
    assert done.result == {"answer": 42}
    assert done.progress == 1.0
    assert ran == [task.id]


async def test_dependencies_gate_execution_and_propagate_failure(client, admin_tokens, engine):
    order = []

    async def ok(ctx):
        order.append(ctx.task.title)
        return None

    async def boom(ctx):
        raise RuntimeError("exploded")

    engine.register_handler("test.ok", ok)
    engine.register_handler("test.boom", boom)

    user = await _admin()
    async with db.sessionmaker() as session:
        service = TaskService(session)
        first = await service.create(user, title="first", handler="test.ok")
        second = await service.create(
            user, title="second", handler="test.ok", depends_on=[first.id]
        )
        failing = await service.create(user, title="failing", handler="test.boom", max_retries=0)
        blocked = await service.create(
            user, title="blocked", handler="test.ok", depends_on=[failing.id]
        )
        await session.commit()

    await _wait_status(second.id, {TaskStatus.COMPLETED})
    assert order.index("first") < order.index("second")

    dead = await _wait_status(blocked.id, {TaskStatus.FAILED})
    assert "Dependency failed" in dead.error


async def test_retry_then_success(client, admin_tokens, engine):
    calls = {"n": 0}

    async def flaky(ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return {"ok": True}

    engine.register_handler("test.flaky", flaky)
    user = await _admin()
    async with db.sessionmaker() as session:
        task = await TaskService(session).create(
            user, title="flaky", handler="test.flaky", max_retries=2
        )
        await session.commit()

    done = await _wait_status(task.id, {TaskStatus.COMPLETED})
    assert calls["n"] == 2
    assert done.attempts == 2


async def test_cancel_running_task(client: AsyncClient, admin_tokens, engine):
    started = asyncio.Event()

    async def slow(ctx):
        started.set()
        await asyncio.sleep(30)

    engine.register_handler("test.slow", slow)
    user = await _admin()
    async with db.sessionmaker() as session:
        task = await TaskService(session).create(user, title="slow", handler="test.slow")
        await session.commit()

    await asyncio.wait_for(started.wait(), timeout=8)
    resp = await client.post(f"/api/v1/tasks/{task.id}/cancel", headers=auth_header(admin_tokens))
    assert resp.status_code == 200
    cancelled = await _wait_status(task.id, {TaskStatus.CANCELLED})
    assert cancelled.status == TaskStatus.CANCELLED


async def test_task_api_validation_and_isolation(client, admin_tokens, user_tokens):
    headers = auth_header(admin_tokens)
    resp = await client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": "研究", "agent": "research", "objective": "check widgets"},
    )
    assert resp.status_code == 201, resp.text
    task_id = resp.json()["id"]

    # Another user cannot see or cancel it.
    resp = await client.get(f"/api/v1/tasks/{task_id}", headers=auth_header(user_tokens))
    assert resp.status_code == 404
    resp = await client.post(f"/api/v1/tasks/{task_id}/cancel", headers=auth_header(user_tokens))
    assert resp.status_code == 404

    # Bad agent name rejected by schema.
    resp = await client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": "x", "agent": "hacker", "objective": "y"},
    )
    assert resp.status_code == 422

    resp = await client.get("/api/v1/tasks/summary", headers=headers)
    assert resp.json()["pending"] == 1
