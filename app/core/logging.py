import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from logging.config import dictConfig

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id.get(),
        }
        for field in ("method", "path", "status", "duration_ms", "error_type"):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload)


def configure_logging(level: str) -> None:
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JsonFormatter}},
            "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
            "loggers": {
                "app": {"handlers": ["console"], "level": level, "propagate": False},
                # Uvicorn access logs include raw query strings; app logs replace them.
                "uvicorn.access": {"handlers": [], "propagate": False},
            },
        }
    )
