"""Optional startup bootstrap: connect one Amazon account from env vars.

The primary way to connect an account is still the API
(``POST /api/v1/amazon/credentials``), which supports multiple accounts and
stores secrets encrypted at rest. This adds a convenience path for a
single-account deployment: if the AMAZON_LWA_* / AMAZON_SELLER_ID env vars
are set, the env vars become the source of truth and are upserted into that
same encrypted storage on every startup - so rotating a secret is "change
the env var and restart," not a manual API call, while everything else
(sync, rate limiting, the REST API) keeps working exactly as it does for an
API-connected account, because it *is* one.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import encrypt_value
from app.models.amazon import AmazonCredential, AmazonRegion
from app.models.user import User

logger = logging.getLogger("jarvis.integrations.amazon.bootstrap")


async def bootstrap_credential_from_env(
    session: AsyncSession, settings: Settings | None = None
) -> AmazonCredential | None:
    """Creates or updates the bootstrap credential; no-ops if unconfigured.

    Never raises for a *missing* configuration (that's the normal case for
    anyone who connects accounts via the API instead) - only for a
    genuinely broken one (e.g. the referenced user doesn't exist), so a
    typo can't silently disable the feature.
    """
    settings = settings or get_settings()
    required = {
        "AMAZON_LWA_CLIENT_ID": settings.amazon_bootstrap_client_id,
        "AMAZON_LWA_CLIENT_SECRET": settings.amazon_bootstrap_client_secret,
        "AMAZON_LWA_REFRESH_TOKEN": settings.amazon_bootstrap_refresh_token,
        "AMAZON_SELLER_ID": settings.amazon_bootstrap_seller_id,
        "AMAZON_BOOTSTRAP_USER_EMAIL": settings.amazon_bootstrap_user_email,
    }
    missing = [name for name, value in required.items() if not value]
    if len(missing) == len(required):
        logger.debug("Amazon credential bootstrap: no env vars set, skipping")
        return None
    if missing:
        logger.warning(
            "Amazon credential bootstrap: partially configured, skipping "
            "(missing: %s) - set all of %s to enable it",
            ", ".join(missing),
            ", ".join(required),
        )
        return None

    user = (
        await session.execute(
            select(User).where(User.email == settings.amazon_bootstrap_user_email)
        )
    ).scalar_one_or_none()
    if user is None:
        logger.error(
            "Amazon credential bootstrap: no user with email '%s' "
            "(AMAZON_BOOTSTRAP_USER_EMAIL) - register that account first",
            settings.amazon_bootstrap_user_email,
        )
        return None

    credential = (
        await session.execute(
            select(AmazonCredential).where(
                AmazonCredential.user_id == user.id,
                AmazonCredential.label == settings.amazon_bootstrap_label,
            )
        )
    ).scalar_one_or_none()

    if credential is None:
        credential = AmazonCredential(user_id=user.id, label=settings.amazon_bootstrap_label)
        session.add(credential)
        action = "Connected"
    else:
        action = "Updated"

    credential.region = AmazonRegion(settings.amazon_bootstrap_region)
    credential.marketplace_id = settings.amazon_bootstrap_marketplace_id
    credential.seller_id = settings.amazon_bootstrap_seller_id
    credential.lwa_client_id = settings.amazon_bootstrap_client_id
    credential.lwa_client_secret_encrypted = encrypt_value(settings.amazon_bootstrap_client_secret)
    credential.lwa_refresh_token_encrypted = encrypt_value(settings.amazon_bootstrap_refresh_token)
    credential.is_active = True
    await session.flush()

    logger.info(
        "%s Amazon account '%s' (%s) for %s from environment variables",
        action,
        credential.label,
        credential.id,
        user.email,
    )
    return credential
