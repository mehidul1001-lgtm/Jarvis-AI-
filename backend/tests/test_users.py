"""Admin user management and RBAC tests."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import auth_header


async def test_regular_user_cannot_list_users(client: AsyncClient, user_tokens: dict):
    resp = await client.get("/api/v1/users", headers=auth_header(user_tokens))
    assert resp.status_code == 403


async def test_admin_lists_users(client: AsyncClient, admin_tokens: dict, user_tokens: dict):
    resp = await client.get("/api/v1/users", headers=auth_header(admin_tokens))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    emails = {item["email"] for item in body["items"]}
    assert emails == {"admin@example.com", "user@example.com"}


async def test_admin_promotes_and_deactivates_user(
    client: AsyncClient, admin_tokens: dict, user_tokens: dict
):
    resp = await client.get("/api/v1/users", headers=auth_header(admin_tokens))
    target = next(u for u in resp.json()["items"] if u["email"] == "user@example.com")

    resp = await client.patch(
        f"/api/v1/users/{target['id']}",
        headers=auth_header(admin_tokens),
        json={"role": "manager"},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "manager"

    resp = await client.patch(
        f"/api/v1/users/{target['id']}",
        headers=auth_header(admin_tokens),
        json={"is_active": False},
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    # Deactivated user loses access immediately.
    resp = await client.get("/api/v1/auth/me", headers=auth_header(user_tokens))
    assert resp.status_code == 401


async def test_admin_cannot_demote_self(client: AsyncClient, admin_tokens: dict):
    resp = await client.get("/api/v1/auth/me", headers=auth_header(admin_tokens))
    admin_id = resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/users/{admin_id}",
        headers=auth_header(admin_tokens),
        json={"role": "user"},
    )
    assert resp.status_code == 422

    resp = await client.patch(
        f"/api/v1/users/{admin_id}",
        headers=auth_header(admin_tokens),
        json={"is_active": False},
    )
    assert resp.status_code == 422


async def test_audit_log_records_auth_events(
    client: AsyncClient, admin_tokens: dict, user_tokens: dict
):
    resp = await client.get("/api/v1/audit", headers=auth_header(admin_tokens))
    assert resp.status_code == 200
    actions = {entry["action"] for entry in resp.json()["items"]}
    assert "auth.register" in actions
    assert "auth.login" in actions

    # Regular users cannot read the audit trail.
    resp = await client.get("/api/v1/audit", headers=auth_header(user_tokens))
    assert resp.status_code == 403
