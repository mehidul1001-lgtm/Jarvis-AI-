"""Application exception hierarchy and FastAPI handlers."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.middleware.request_context import SECURITY_HEADERS

logger = logging.getLogger("jarvis.errors")


class JarvisError(Exception):
    """Base class for all domain errors."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "bad_request"

    def __init__(self, message: str = "Bad request") -> None:
        self.message = message
        super().__init__(message)


class AuthenticationError(JarvisError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "authentication_failed"

    def __init__(self, message: str = "Authentication failed") -> None:
        super().__init__(message)


class AuthorizationError(JarvisError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"

    def __init__(self, message: str = "Not enough permissions") -> None:
        super().__init__(message)


class NotFoundError(JarvisError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"

    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(message)


class ConflictError(JarvisError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"

    def __init__(self, message: str = "Resource conflict") -> None:
        super().__init__(message)


class ValidationFailedError(JarvisError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "validation_failed"

    def __init__(self, message: str = "Validation failed") -> None:
        super().__init__(message)


class RateLimitExceededError(JarvisError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limit_exceeded"

    def __init__(self, message: str = "Too many requests, slow down") -> None:
        super().__init__(message)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(JarvisError)
    async def jarvis_error_handler(request: Request, exc: JarvisError) -> JSONResponse:
        headers = {}
        if isinstance(exc, AuthenticationError):
            headers["WWW-Authenticate"] = "Bearer"
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=headers,
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # Starlette's ServerErrorMiddleware sits outside RequestContextMiddleware,
        # so on an unhandled exception that middleware's post-call_next code
        # (request-id header, security headers, completion log) never runs.
        # Reapply it here so 500 responses stay consistent with every other
        # response. Never leak internals to the client; log the full
        # traceback instead.
        request_id = getattr(request.state, "request_id", None)
        logger.exception(
            "Unhandled error on %s %s",
            request.method,
            request.url.path,
            extra={"request_id": request_id, "method": request.method, "path": request.url.path},
        )
        headers = dict(SECURITY_HEADERS)
        if request_id:
            headers["X-Request-ID"] = request_id
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An internal error occurred",
                }
            },
            headers=headers,
        )
