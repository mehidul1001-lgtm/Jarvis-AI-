"""Structured JSON logging configuration."""
from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from app.core.config import get_settings


class JsonFormatter(logging.Formatter):
    """Renders log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        for attr in ("request_id", "method", "path", "status_code", "duration_ms", "user_id"):
            value = getattr(record, attr, None)
            if value is not None:
                entry[attr] = value
        return json.dumps(entry, default=str)


def configure_logging() -> None:
    settings = get_settings()
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())

    handler = logging.StreamHandler(sys.stdout)
    if settings.environment == "development":
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s - %(message)s")
        )
    else:
        handler.setFormatter(JsonFormatter())

    root.handlers = [handler]
    # Uvicorn's access log duplicates our request logging middleware.
    logging.getLogger("uvicorn.access").disabled = True
