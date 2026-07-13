"""Task queue API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession
from app.models.task import TaskStatus
from app.schemas.ai import TaskCreate, TaskOut
from app.schemas.common import Page
from app.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(payload: TaskCreate, user: CurrentUser, db: DbSession) -> TaskOut:
    task = await TaskService(db).create(
        user,
        title=payload.title,
        handler="agent.run",
        description=payload.objective,
        payload={"agent": payload.agent, "objective": payload.objective},
        priority=payload.priority,
    )
    return TaskOut.model_validate(task)


@router.get("", response_model=Page[TaskOut])
async def list_tasks(
    user: CurrentUser,
    db: DbSession,
    status_filter: TaskStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[TaskOut]:
    tasks, total = await TaskService(db).list(
        user, status=status_filter, page=page, page_size=page_size
    )
    return Page(
        items=[TaskOut.model_validate(t) for t in tasks],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/summary")
async def task_summary(user: CurrentUser, db: DbSession) -> dict:
    return await TaskService(db).counts_by_status(user)


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(task_id: uuid.UUID, user: CurrentUser, db: DbSession) -> TaskOut:
    return TaskOut.model_validate(await TaskService(db).get(user, task_id))


@router.post("/{task_id}/cancel", response_model=TaskOut)
async def cancel_task(task_id: uuid.UUID, user: CurrentUser, db: DbSession) -> TaskOut:
    return TaskOut.model_validate(await TaskService(db).cancel(user, task_id))
