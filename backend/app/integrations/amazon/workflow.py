"""Workflow engine handlers for Amazon SP-API sync.

Registers a single ``amazon.sync`` handler parameterized by which resource
to pull. The workflow engine has no native cron scheduler, so recurring
sync is implemented by having a successful run enqueue its own successor
``amazon_sync_interval_minutes`` later — the same self-rescheduling pattern
already used nowhere else in this codebase but is the natural extension of
``scheduled_for`` on :class:`WorkflowTask`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import get_settings
from app.models.amazon import AmazonCredential
from app.services.amazon_sync_service import AmazonSyncService
from app.workflows.engine import TaskRunContext, WorkflowEngine

logger = logging.getLogger("jarvis.integrations.amazon.workflow")

RESOURCE_HANDLERS = {
    "orders": "sync_orders",
    "inventory": "sync_inventory",
    "fba_shipments": "sync_fba_shipments",
    "financial_events": "sync_financial_events",
}


async def _load_credential(ctx: TaskRunContext, credential_id: str) -> AmazonCredential:
    credential = await ctx.db.get(AmazonCredential, uuid.UUID(credential_id))
    if credential is None or credential.user_id != ctx.user.id:
        raise ValueError(f"Amazon credential '{credential_id}' not found")
    if not credential.is_active:
        raise ValueError(f"Amazon credential '{credential.label}' is disabled")
    return credential


async def run_amazon_sync(ctx: TaskRunContext) -> dict[str, Any]:
    payload = ctx.task.payload or {}
    credential_id = str(payload.get("credential_id", ""))
    resource = str(payload.get("resource", "all"))
    recurring = bool(payload.get("recurring", False))

    credential = await _load_credential(ctx, credential_id)
    async with AmazonSyncService(ctx.db, credential) as service:
        if resource == "all":
            results = await service.sync_all()
        elif resource in RESOURCE_HANDLERS:
            results = [await getattr(service, RESOURCE_HANDLERS[resource])()]
        else:
            raise ValueError(f"Unknown Amazon sync resource '{resource}'")

    if recurring:
        await _reschedule(ctx, credential_id, resource)

    return {"credential_id": credential_id, "results": [r.as_dict() for r in results]}


async def _reschedule(ctx: TaskRunContext, credential_id: str, resource: str) -> None:
    from app.services.task_service import TaskService

    settings = get_settings()
    next_run = datetime.now(UTC) + timedelta(minutes=settings.amazon_sync_interval_minutes)
    await TaskService(ctx.db).create(
        ctx.user,
        title=f"Amazon sync ({resource})",
        handler="amazon.sync",
        payload={"credential_id": credential_id, "resource": resource, "recurring": True},
        scheduled_for=next_run,
    )
    logger.info(
        "Rescheduled recurring Amazon sync '%s' for credential %s at %s",
        resource,
        credential_id,
        next_run.isoformat(),
    )


def register_amazon_workflow_handlers(engine: WorkflowEngine) -> None:
    engine.register_handler("amazon.sync", run_amazon_sync)
