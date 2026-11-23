import logging
import re
from time import perf_counter
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_response
from app.core.logging import request_id
from app.core.telemetry import group

logger = logging.getLogger("app.requests")
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", re.ASCII)


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, telemetry=None) -> None:
        self.app = app
        self.telemetry = telemetry

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming_id = Headers(scope=scope).get("x-request-id", "")
        current_id = incoming_id if REQUEST_ID_PATTERN.fullmatch(incoming_id) else str(uuid4())
        token = request_id.set(current_id)
        start = perf_counter()
        status = 500
        response_started = False

        async def send_response(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = current_id
                if scope["path"] == "/admin" or scope["path"].startswith("/v1/admin/"):
                    headers = MutableHeaders(scope=message)
                    headers["Cache-Control"] = "no-store"
                    headers["Referrer-Policy"] = "no-referrer"
                    headers["X-Content-Type-Options"] = "nosniff"
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_response)
        except Exception as exc:
            # Exception strings/tracebacks may contain credentials or submitted content.
            logger.error("request_failed", extra={"error_type": type(exc).__name__})
            if response_started:
                raise
            response = error_response(500, "internal_error", "An unexpected error occurred")
            await response(scope, receive, send_response)
        finally:
            logger.info(
                "request_completed",
                extra={
                    "method": scope["method"],
                    "path": getattr(scope.get("route"), "path", "/<unmatched>"),
                    "status": status,
                    "duration_ms": round((perf_counter() - start) * 1000, 3),
                },
            )
            try:
                if self.telemetry is not None:
                    await self.telemetry.record(
                        "http." + group(scope["path"]),
                        milliseconds=(perf_counter() - start) * 1000,
                        error=status >= 500,
                        rejected=int(400 <= status < 500),
                    )
            finally:
                request_id.reset(token)
