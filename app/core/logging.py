import json
import logging
import re
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
            "message": record.msg
            if isinstance(record.msg, str)
            and not record.args
            and record.msg
            in {"request_completed", "request_failed", "mcp_tool_failed", "job attempt failed"}
            else "application_event",
            "request_id": request_id.get(),
        }
        for field in ("method", "path", "status", "duration_ms", "error_type", "job_id"):
            if hasattr(record, field):
                value = getattr(record, field)
                valid = (
                    field in {"status", "duration_ms"}
                    and type(value) in {int, float}
                    and 0 <= value <= 10**12
                ) or (
                    isinstance(value, str)
                    and (
                        field == "method"
                        and value in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
                        or field == "path"
                        and re.fullmatch(r"/[A-Za-z0-9_/{}/<>.-]{0,180}", value)
                        or field == "error_type"
                        and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value)
                        or field == "job_id"
                        and re.fullmatch(r"[0-9a-f-]{36}", value)
                    )
                )
                if valid:
                    payload[field] = value
        return json.dumps(payload)


def configure_logging(level: str) -> None:
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JsonFormatter}},
            "handlers": {
                "console": {"class": "logging.StreamHandler", "formatter": "json"},
                "discard": {"class": "logging.NullHandler"},
            },
            "loggers": {
                "app": {"handlers": ["console"], "level": level, "propagate": False},
                # Uvicorn access logs include raw query strings; app logs replace them.
                "uvicorn.access": {"handlers": [], "propagate": False},
                # Tool input/errors can contain report text. Only our sanitized
                # app.mcp metadata is logged, never SDK exception tracebacks.
                "mcp": {"handlers": ["discard"], "level": "CRITICAL", "propagate": False},
                **{
                    name: {"handlers": ["discard"], "propagate": False}
                    for name in (
                        "arq",
                        "httpx",
                        "httpcore",
                        "botocore",
                        "boto3",
                        "urllib3",
                        "sqlalchemy.engine",
                    )
                },
            },
        }
    )
