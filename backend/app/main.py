"""JARVIS AI backend application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.database import db
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.rate_limit import RateLimitMiddleware
from app.middleware.request_context import RequestContextMiddleware

logger = logging.getLogger("jarvis")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.agents.registry import get_agent_registry, register_workflow_handlers
    from app.ai.llm import get_llm_client
    from app.integrations.amazon.workflow import register_amazon_workflow_handlers
    from app.workflows.engine import get_workflow_engine

    settings = get_settings()
    configure_logging()
    db.init()

    get_agent_registry()  # load agents
    engine = get_workflow_engine()
    register_workflow_handlers(engine)
    register_amazon_workflow_handlers(engine)
    await engine.start()

    if not get_llm_client().available:
        logger.warning(
            "JARVIS_ANTHROPIC_API_KEY is not set — AI chat and agents are disabled "
            "until it is configured"
        )
    logger.info(
        "JARVIS AI backend %s starting (environment=%s)",
        __version__,
        settings.environment,
    )
    yield
    await engine.stop()
    await db.dispose()
    logger.info("JARVIS AI backend stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url="/redoc" if settings.environment != "production" else None,
        openapi_url="/openapi.json" if settings.environment != "production" else None,
    )

    # Starlette runs middleware in reverse registration order, so requests
    # flow CORS -> request context/logging -> rate limit -> app. This keeps
    # CORS headers on every response (browsers cannot read 429s without
    # them) and ensures rate-limited responses are still logged and carry
    # security headers and a request ID.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
