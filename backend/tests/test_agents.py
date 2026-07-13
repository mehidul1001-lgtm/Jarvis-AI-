"""Agent framework tests: loading, health, execution, tool permissions."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from app.agents.registry import get_agent_registry
from app.core.database import db
from app.models.memory import MemoryEntry, MemoryKind
from app.models.task import TaskStatus
from app.models.user import User
from app.services.task_service import TaskService
from app.workflows.engine import get_workflow_engine
from tests.conftest import auth_header
from tests.fake_llm import text_response, tool_response


def test_all_six_agents_load_with_health():
    registry = get_agent_registry()
    names = set(registry.agents)
    assert names == {"amazon", "finance", "developer", "research", "operations", "marketing"}
    for health in registry.health():
        assert set(health) >= {
            "name",
            "display_name",
            "healthy",
            "available",
            "runs_completed",
            "runs_failed",
            "tools",
        }
        # Every agent carries the builtin tool set.
        assert "memory_search" in health["tools"]


def test_domain_tools_are_agent_specific():
    registry = get_agent_registry()
    amazon_tools = registry.get("amazon").health()["tools"]
    finance_tools = registry.get("finance").health()["tools"]
    assert "fba_profitability" in amazon_tools
    assert "fba_profitability" not in finance_tools
    assert "cashflow_projection" in finance_tools
    assert "amazon_sales_summary" in amazon_tools
    assert "amazon_inventory_status" in amazon_tools
    assert "amazon_sales_summary" not in finance_tools


async def test_agent_run_via_workflow_engine(client, admin_tokens, fake_llm):
    """End-to-end: task queued → engine runs agent → tools execute →
    outcome persisted to memory and task completed."""
    fake_llm.queue(
        tool_response(
            "fba_profitability",
            {"sale_price": 25.0, "landed_cost": 5.0, "fba_fee": 4.5},
        ),
        text_response(
            "At $25 with $5 landed cost and $4.50 FBA fee you net $11.75/unit (47% margin)."
        ),
    )
    fake_llm.default = text_response("Done.")

    engine = get_workflow_engine()
    await engine.start()
    try:
        async with db.sessionmaker() as session:
            user = (
                await session.execute(select(User).where(User.email == "admin@example.com"))
            ).scalar_one()
            task = await TaskService(session).create(
                user,
                title="Check widget margins",
                handler="agent.run",
                payload={
                    "agent": "amazon",
                    "objective": "Evaluate profitability of the widget at $25",
                },
            )
            await session.commit()

        deadline = asyncio.get_event_loop().time() + 10
        final = None
        while asyncio.get_event_loop().time() < deadline:
            async with db.sessionmaker() as session:
                from app.models.task import WorkflowTask

                final = await session.get(WorkflowTask, task.id)
                if final.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                    break
            await asyncio.sleep(0.05)

        assert final is not None and final.status == TaskStatus.COMPLETED, final and final.error
        assert final.result["agent"] == "amazon"
        assert "47% margin" in final.result["summary"]
        assert final.result["confidence"] > 0.5

        # Memory connector wrote the outcome as a task note.
        async with db.sessionmaker() as session:
            notes = (
                (
                    await session.execute(
                        select(MemoryEntry).where(MemoryEntry.kind == MemoryKind.TASK_NOTE)
                    )
                )
                .scalars()
                .all()
            )
            assert len(notes) == 1
            assert notes[0].source == "agent:amazon"
    finally:
        await engine.stop()

    agent = get_agent_registry().get("amazon")
    assert agent.stats.runs_completed >= 1
    assert agent.stats.last_heartbeat is not None


async def test_amazon_fee_math_is_correct(client, admin_tokens):
    from app.ai.tools.registry import ToolContext

    registry = get_agent_registry()
    agent = registry.get("amazon")
    async with db.sessionmaker() as session:
        user = (
            await session.execute(select(User).where(User.email == "admin@example.com"))
        ).scalar_one()
        ctx = ToolContext(db=session, user=user, agent="amazon")
        result = await agent.tools.execute(
            "fba_profitability",
            {"sale_price": 25.0, "landed_cost": 5.0, "fba_fee": 4.5},
            ctx,
        )
        data = json.loads(result.content)
        assert data["referral_fee"] == 3.75
        assert data["net_profit_per_unit"] == 11.75
        assert data["margin_pct"] == 47.0


async def test_amazon_sales_summary_tool_reflects_real_synced_data(client, admin_tokens):
    """The amazon agent's data tools must read real synced rows, not guess -
    this is what makes it a live business integration rather than Phase 2's
    memory-only agent."""
    from datetime import UTC, datetime

    from app.ai.tools.registry import ToolContext
    from app.core.security import encrypt_value
    from app.models.amazon import AmazonCredential, AmazonOrder, AmazonRegion

    async with db.sessionmaker() as session:
        user = (
            await session.execute(select(User).where(User.email == "admin@example.com"))
        ).scalar_one()

        assert (
            await get_agent_registry()
            .get("amazon")
            .tools.execute(
                "amazon_sales_summary", {}, ToolContext(db=session, user=user, agent="amazon")
            )
        ).content == "No Amazon Seller Central account is connected yet."

        credential = AmazonCredential(
            user_id=user.id,
            label="Test Store",
            region=AmazonRegion.NA,
            marketplace_id="ATVPDKIKX0DER",
            seller_id="A1B2C3D4E5",
            lwa_client_id="fake-client-id",
            lwa_client_secret_encrypted=encrypt_value("fake-secret"),
            lwa_refresh_token_encrypted=encrypt_value("fake-refresh"),
        )
        session.add(credential)
        await session.flush()
        session.add(
            AmazonOrder(
                credential_id=credential.id,
                amazon_order_id="999-0000000-0000001",
                purchase_date=datetime.now(UTC),
                last_update_date=datetime.now(UTC),
                order_status="Shipped",
                marketplace_id="ATVPDKIKX0DER",
                order_total_amount="88.00",
                order_total_currency="USD",
            )
        )
        await session.commit()

        ctx = ToolContext(db=session, user=user, agent="amazon")
        result = (
            await get_agent_registry()
            .get("amazon")
            .tools.execute("amazon_sales_summary", {"days": 30}, ctx)
        )
        data = json.loads(result.content)
        assert data["order_count"] == 1
        assert data["total_revenue"] == "88.00"


async def test_tool_role_permissions_enforced(client, admin_tokens, user_tokens):
    """A role-restricted tool is refused for lower roles and audited."""
    from app.ai.tools.registry import ToolContext, ToolRegistry, ToolSpec
    from app.models.user import UserRole

    registry = ToolRegistry("restricted-test")

    async def secret_tool(ctx):
        return "top secret"

    registry.register(
        ToolSpec(
            name="admin_only",
            description="admin only",
            input_schema={"type": "object", "properties": {}},
            handler=secret_tool,
            allowed_roles=(UserRole.ADMIN,),
        )
    )

    async with db.sessionmaker() as session:
        admin = (
            await session.execute(select(User).where(User.email == "admin@example.com"))
        ).scalar_one()
        regular = (
            await session.execute(select(User).where(User.email == "user@example.com"))
        ).scalar_one()

        allowed = await registry.execute("admin_only", {}, ToolContext(db=session, user=admin))
        assert allowed.content == "top secret" and not allowed.is_error

        denied = await registry.execute("admin_only", {}, ToolContext(db=session, user=regular))
        assert denied.is_error and "does not permit" in denied.content

        # Restricted tools are not even advertised to lower roles.
        assert registry.definitions_for(regular) == []
        await session.commit()

    # Denial was audited.
    resp = await client.get(
        "/api/v1/audit",
        headers=auth_header(admin_tokens),
        params={"action": "ai.tool_denied"},
    )
    assert resp.json()["total"] == 1


async def test_agents_api_lists_status(client, admin_tokens):
    resp = await client.get("/api/v1/agents", headers=auth_header(admin_tokens))
    assert resp.status_code == 200
    agents = resp.json()
    assert len(agents) == 6
    assert all(agent["available"] is False for agent in agents)  # no API key in tests
