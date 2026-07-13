"""Env-var Amazon credential bootstrap: no-op when unset, safe on partial
config or an unknown user, idempotent (create-then-update-in-place) when
fully configured.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.config import Settings
from app.core.database import db
from app.integrations.amazon.bootstrap import bootstrap_credential_from_env
from app.models.amazon import AmazonCredential
from app.models.user import User

FULL_SETTINGS_KWARGS = {
    "AMAZON_LWA_CLIENT_ID": "amzn1.application-oa2-client.fake",
    "AMAZON_LWA_CLIENT_SECRET": "initial-secret",
    "AMAZON_LWA_REFRESH_TOKEN": "Atzr|initial-refresh",
    "AMAZON_SELLER_ID": "A1B2C3D4E5",
    "AMAZON_BOOTSTRAP_USER_EMAIL": "admin@example.com",
}


async def _admin() -> User:
    async with db.sessionmaker() as session:
        return (
            await session.execute(select(User).where(User.email == "admin@example.com"))
        ).scalar_one()


async def test_bootstrap_noop_when_unset(client, admin_tokens):
    async with db.sessionmaker() as session:
        result = await bootstrap_credential_from_env(session, Settings())
        assert result is None
        rows = (await session.execute(select(AmazonCredential))).scalars().all()
        assert rows == []


async def test_bootstrap_noop_on_partial_config(client, admin_tokens):
    partial = dict(FULL_SETTINGS_KWARGS)
    del partial["AMAZON_SELLER_ID"]
    async with db.sessionmaker() as session:
        result = await bootstrap_credential_from_env(session, Settings(**partial))
        assert result is None
        rows = (await session.execute(select(AmazonCredential))).scalars().all()
        assert rows == []


async def test_bootstrap_skips_unknown_user(client, admin_tokens):
    settings = Settings(**{**FULL_SETTINGS_KWARGS, "AMAZON_BOOTSTRAP_USER_EMAIL": "nobody@x.com"})
    async with db.sessionmaker() as session:
        result = await bootstrap_credential_from_env(session, settings)
        assert result is None
        rows = (await session.execute(select(AmazonCredential))).scalars().all()
        assert rows == []


async def test_bootstrap_creates_credential(client, admin_tokens):
    user = await _admin()
    async with db.sessionmaker() as session:
        credential = await bootstrap_credential_from_env(session, Settings(**FULL_SETTINGS_KWARGS))
        await session.commit()

    assert credential is not None
    assert credential.user_id == user.id
    assert credential.seller_id == "A1B2C3D4E5"
    assert credential.lwa_client_secret_encrypted != "initial-secret"
    assert credential.lwa_refresh_token_encrypted != "Atzr|initial-refresh"
    assert credential.is_active is True


async def test_bootstrap_is_idempotent_and_updates_in_place(client, admin_tokens):
    async with db.sessionmaker() as session:
        first = await bootstrap_credential_from_env(session, Settings(**FULL_SETTINGS_KWARGS))
        await session.commit()
        first_id = first.id
        first_secret = first.lwa_client_secret_encrypted

    updated_kwargs = {**FULL_SETTINGS_KWARGS, "AMAZON_LWA_CLIENT_SECRET": "rotated-secret"}
    async with db.sessionmaker() as session:
        second = await bootstrap_credential_from_env(session, Settings(**updated_kwargs))
        await session.commit()
        second_id = second.id
        second_secret = second.lwa_client_secret_encrypted

    assert first_id == second_id
    assert first_secret != second_secret

    async with db.sessionmaker() as session:
        rows = (await session.execute(select(AmazonCredential))).scalars().all()
        assert len(rows) == 1


async def test_bootstrap_is_reachable_from_default_settings_signature(client, admin_tokens):
    """Guards the ``settings: Settings | None = None`` default path (used by
    the real app startup, which calls this with no settings argument)."""
    async with db.sessionmaker() as session:
        result = await bootstrap_credential_from_env(session)
        assert result is None
