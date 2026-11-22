"""Bound HTTP ingestion before JSON parsing; keep health independent of quotas."""

import asyncio
import time

from starlette.datastructures import Headers

from app.core.errors import error_response
from app.media import MAX_BYTES


class IngressMiddleware:
    def __init__(self, app, settings):
        self.app = app
        self.policy = settings.operations
        self.enabled = self.policy.enabled and settings.environment == "production"
        self.active = self.media_active = 0
        self.window = -1
        self.used = 0

    async def reject(self, scope, receive, send, status, code, message, retry=None):
        headers = {"Cache-Control": "no-store"}
        if retry:
            headers["Retry-After"] = str(retry)
        await error_response(status, code, message, headers=headers)(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in {"/health", "/ready"}:
            return await self.app(scope, receive, send)
        media = (
            scope["method"] == "POST"
            and scope["path"].startswith("/v1/reports/")
            and scope["path"].endswith("/attachments")
        )
        now = int(time.time())
        window = now // 60
        if self.enabled:
            if window != self.window:
                self.window = window
                self.used = 0
            if self.used >= self.policy.http_per_minute:
                return await self.reject(
                    scope,
                    receive,
                    send,
                    429,
                    "rate_limited",
                    "Request allowance exceeded; retry later.",
                    60 - now % 60,
                )
            self.used += 1
            if self.active >= self.policy.http_inflight or media and self.media_active >= 1:
                return await self.reject(
                    scope,
                    receive,
                    send,
                    503,
                    "capacity",
                    "Request capacity reached; retry later.",
                    1,
                )
        self.active += 1
        self.media_active += int(media)
        try:
            headers = Headers(scope=scope)
            if headers.get("content-encoding", "identity").lower() != "identity":
                return await self.reject(
                    scope,
                    receive,
                    send,
                    415,
                    "unsupported_encoding",
                    "Compressed request bodies are not accepted.",
                )
            lengths = headers.getlist("content-length")
            if (
                len(lengths) > 1
                or lengths
                and (not lengths[0].isascii() or not lengths[0].isdigit() or len(lengths[0]) > 20)
            ):
                return await self.reject(
                    scope, receive, send, 400, "invalid_length", "Invalid request length."
                )
            maximum = MAX_BYTES if media else self.policy.json_bytes
            if lengths and int(lengths[0]) > maximum:
                return await self.reject(
                    scope,
                    receive,
                    send,
                    413,
                    "request_too_large",
                    "Request body exceeds its limit.",
                )
            # Media's existing incremental reader/decoder enforces its own 5 MiB cap.
            # Do not duplicate a multi-megabyte image buffer in middleware.
            if media:
                return await self.app(scope, receive, send)
            chunks = []
            size = 0
            try:
                async with asyncio.timeout(self.policy.body_seconds):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        body = message.get("body", b"")
                        size += len(body)
                        if size > maximum:
                            return await self.reject(
                                scope,
                                receive,
                                send,
                                413,
                                "request_too_large",
                                "Request body exceeds its limit.",
                            )
                        chunks.append(body)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                return await self.reject(
                    scope, receive, send, 408, "request_timeout", "Request body timed out."
                )
            delivered = False

            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
                return await receive()

            await self.app(scope, bounded_receive, send)
        finally:
            self.active -= 1
            self.media_active -= int(media)
