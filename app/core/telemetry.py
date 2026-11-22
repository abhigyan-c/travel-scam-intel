"""Low-cardinality UTC-day counters; no raw text, IDs, URLs, tokens or IPs."""

import asyncio
import math
import time
from datetime import UTC, datetime

HTTP_GROUPS = {
    "health",
    "ready",
    "mcp",
    "admin",
    "reports",
    "media",
    "locations",
    "patterns",
    "other",
}
TOOLS = {
    "open_scam_report_form",
    "request_scam_report",
    "watch_scam_report",
    "get_scam_report",
    "wait_for_scam_report",
    "resolve_scam_location",
    "search_scam_patterns",
    "get_scam_pattern",
    "get_scam_evidence",
    "match_possible_scam",
    "get_recent_patterns",
    "submit_scam_report",
    "other",
}
NAMES = (
    {f"http.{value}" for value in HTTP_GROUPS}
    | {f"mcp.{value}" for value in TOOLS}
    | {
        "provider.search",
        "provider.fetch",
        "provider.sarvam",
        "provider.embedding",
        "worker.job",
        "scheduler.cycle",
    }
)
FIELDS = {
    "count",
    "errors",
    "rejected",
    "retries",
    "duration_ms",
    "le_100",
    "le_500",
    "le_2000",
    "le_10000",
    "gt_10000",
    "requests",
    "results",
    "tokens",
    "usage_unknown",
    "texts",
    "queued",
}


def group(path):
    if path in {"/health", "/ready", "/mcp"}:
        return path[1:]
    if path == "/admin" or path.startswith("/v1/admin/"):
        return "admin"
    if path.startswith("/v1/reports/") and path.endswith("/attachments"):
        return "media"
    if path.startswith("/v1/reports"):
        return "reports"
    if path.startswith("/v1/locations"):
        return "locations"
    if path.startswith("/v1/patterns") or path.startswith("/v1/hypotheses"):
        return "patterns"
    return "other"


class Telemetry:
    def __init__(self, redis, *, clock=lambda: datetime.now(UTC)):
        self.redis = redis
        self.clock = clock

    def key(self):
        return "tsi:metrics:v1:" + self.clock().date().isoformat()

    async def record(self, name, *, milliseconds=0, error=False, **numbers):
        if name not in NAMES:
            raise ValueError("unbounded_metric_name")
        bucket = next(
            (f"le_{bound}" for bound in (100, 500, 2000, 10000) if milliseconds <= bound),
            "gt_10000",
        )
        values = {
            "count": 1,
            "errors": int(error),
            "duration_ms": round(max(0, milliseconds)),
            bucket: 1,
            **numbers,
        }
        values = {
            key: int(value)
            for key, value in values.items()
            if key in FIELDS
            and type(value) in {int, float}
            and math.isfinite(value)
            and 0 <= value <= 10**12
        }
        try:
            store = self.redis()
            if store is None:
                return False
            async with asyncio.timeout(0.15):
                pipe = store.pipeline(transaction=False)
                day_key = self.key()
                for key, value in values.items():
                    pipe.hincrby(day_key, name + "." + key, value)
                pipe.expire(day_key, 172800)
                await pipe.execute()
            return True
        except Exception:
            return False

    async def snapshot(self):
        try:
            store = self.redis()
            if store is None:
                raise ConnectionError
            async with asyncio.timeout(0.5):
                raw = await store.hgetall(self.key())
            values = {}
            for key, value in raw.items():
                key = key.decode() if isinstance(key, bytes) else key
                name, field = key.rsplit(".", 1)
                if name in NAMES and field in FIELDS:
                    values[key] = int(value)
            return {"available": True, "utc_day": self.clock().date().isoformat(), "values": values}
        except Exception:
            return {"available": False, "utc_day": self.clock().date().isoformat(), "values": None}


class ObservedClient:
    def __init__(self, client, telemetry, kind):
        self.client, self.telemetry, self.kind = client, telemetry, kind

    def __getattr__(self, name):
        return getattr(self.client, name)

    async def _call(self, method, *args, **kwargs):
        started = time.perf_counter()
        error = False
        numbers = {}
        try:
            result = await getattr(self.client, method)(*args, **kwargs)
            if method == "complete":
                error = bool(result.error)
                numbers = {"requests": result.requests}
                if "total_tokens" in result.usage:
                    numbers["tokens"] = result.usage["total_tokens"]
                elif result.requests:
                    numbers["usage_unknown"] = 1
            elif method == "search":
                error = bool(result.usage.error)
                numbers = {"requests": result.usage.requests, "results": len(result.results)}
            elif method == "embed":
                numbers = {"texts": len(args[0])}
            return result
        except BaseException:
            error = True
            if method == "complete":
                numbers["usage_unknown"] = 1
            raise
        finally:
            await self.telemetry.record(
                "provider." + self.kind,
                milliseconds=(time.perf_counter() - started) * 1000,
                error=error,
                **numbers,
            )

    async def complete(self, **kwargs):
        return await self._call("complete", **kwargs)

    async def search(self, *args, **kwargs):
        return await self._call("search", *args, **kwargs)

    async def fetch(self, *args, **kwargs):
        return await self._call("fetch", *args, **kwargs)

    async def embed(self, *args, **kwargs):
        return await self._call("embed", *args, **kwargs)
