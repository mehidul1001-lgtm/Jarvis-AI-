"""Authentication flow tests: register, login, refresh rotation, logout, RBAC."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import REGISTER_PAYLOAD, auth_header


async def test_first_user_becomes_admin(client: AsyncClient):
    resp = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
    assert resp.status_code == 201
    assert resp.json()["role"] == "admin"

    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "second@example.com",
            "full_name": "Second User",
            "password": "SecondPass123",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "user"


async def test_duplicate_email_rejected(client: AsyncClient):
    await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
    resp = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


async def test_weak_password_rejected(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "weak@example.com", "full_name": "Weak", "password": "short"},
    )
    assert resp.status_code == 422


async def test_login_and_me(client: AsyncClient, admin_tokens: dict):
    resp = await client.get("/api/v1/auth/me", headers=auth_header(admin_tokens))
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == REGISTER_PAYLOAD["email"]
    assert body["role"] == "admin"


async def test_login_wrong_password(client: AsyncClient, admin_tokens: dict):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": REGISTER_PAYLOAD["email"], "password": "WrongPass123"},
    )
    assert resp.status_code == 401
    # The message must not reveal whether the account exists.
    unknown = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "WrongPass123"},
    )
    assert unknown.status_code == 401
    assert resp.json() == unknown.json()


async def test_me_requires_token(client: AsyncClient):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401

    resp = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-token"})
    assert resp.status_code == 401


async def test_refresh_rotates_tokens(client: AsyncClient, admin_tokens: dict):
    resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": admin_tokens["refresh_token"]}
    )
    assert resp.status_code == 200
    new_tokens = resp.json()
    assert new_tokens["refresh_token"] != admin_tokens["refresh_token"]

    # New pair works.
    resp = await client.get("/api/v1/auth/me", headers=auth_header(new_tokens))
    assert resp.status_code == 200

    # Old refresh token is dead after rotation...
    resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": admin_tokens["refresh_token"]}
    )
    assert resp.status_code == 401

    # ...and reuse detection revoked everything, including the new session.
    resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert resp.status_code == 401


async def test_logout_revokes_session(client: AsyncClient, admin_tokens: dict):
    resp = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": admin_tokens["refresh_token"]}
    )
    assert resp.status_code == 200

    # Access token bound to the revoked session stops working immediately.
    resp = await client.get("/api/v1/auth/me", headers=auth_header(admin_tokens))
    assert resp.status_code == 401

    resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": admin_tokens["refresh_token"]}
    )
    assert resp.status_code == 401


async def test_password_change_revokes_sessions(client: AsyncClient, admin_tokens: dict):
    resp = await client.post(
        "/api/v1/auth/me/password",
        headers=auth_header(admin_tokens),
        json={
            "current_password": REGISTER_PAYLOAD["password"],
            "new_password": "BrandNewPass123",
        },
    )
    assert resp.status_code == 200

    # All sessions (including this one) are revoked.
    resp = await client.get("/api/v1/auth/me", headers=auth_header(admin_tokens))
    assert resp.status_code == 401

    # New password works.
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": REGISTER_PAYLOAD["email"], "password": "BrandNewPass123"},
    )
    assert resp.status_code == 200


async def test_session_listing_and_revocation(client: AsyncClient, admin_tokens: dict):
    resp = await client.get("/api/v1/auth/me/sessions", headers=auth_header(admin_tokens))
    assert resp.status_code == 200
    sessions = resp.json()
    assert len(sessions) == 1

    resp = await client.delete(
        f"/api/v1/auth/me/sessions/{sessions[0]['id']}",
        headers=auth_header(admin_tokens),
    )
    assert resp.status_code == 200
    resp = await client.get("/api/v1/auth/me", headers=auth_header(admin_tokens))
    assert resp.status_code == 401
