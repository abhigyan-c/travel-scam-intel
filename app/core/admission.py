"""Resource admission for anonymous traffic; no identities or submitted text are stored."""

import asyncio
import hashlib
import time

from pydantic import BaseModel, ConfigDict, Field


class OperationalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    http_per_minute: int = Field(default=240, ge=1, le=10000)
    http_inflight: int = Field(default=16, ge=1, le=128)
    json_bytes: int = Field(default=65536, ge=8192, le=1048576)
    body_seconds: float = Field(default=10, gt=0, le=30)
    reports_per_ten_minutes: int = Field(default=5, ge=1, le=1000)
    reports_per_day: int = Field(default=40, ge=1, le=10000)
    research_per_ten_minutes: int = Field(default=6, ge=1, le=1000)
    research_per_day: int = Field(default=100, ge=1, le=10000)
    watches_per_ten_minutes: int = Field(default=120, ge=1, le=10000)
    watches_per_day: int = Field(default=1000, ge=1, le=100000)


class AdmissionError(Exception):
    def __init__(self, unavailable=False):
        self.unavailable = unavailable
        self.status = 503 if unavailable else 429
        super().__init__("admission_unavailable" if unavailable else "rate_limited")


# Both windows are checked before either counter is incremented. Retry receipts are
# hashed and bounded by admitted requests; rejected random keys cannot grow the hash.
SCRIPT = """
for i=1,2 do
 if redis.call('HEXISTS',KEYS[i],ARGV[1]) == 0 then
  local used=tonumber(redis.call('HGET',KEYS[i],':count') or '0')
  if used>=tonumber(ARGV[i+1]) then return 0 end
 end
end
for i=1,2 do
 if redis.call('HEXISTS',KEYS[i],ARGV[1]) == 0 then
  redis.call('HINCRBY',KEYS[i],':count',1)
  redis.call('HSET',KEYS[i],ARGV[1],1)
 end
 redis.call('EXPIRE',KEYS[i],tonumber(ARGV[i+3])+1)
end
return 1
"""


class Admission:
    def __init__(self, settings, redis, *, clock=time.time):
        self.policy = settings.operations
        self.enabled = self.policy.enabled and settings.environment == "production"
        self.redis = redis
        self.clock = clock

    async def require(self, kind, receipt):
        if not self.enabled:
            return
        if kind not in {"reports", "research", "watches"}:
            raise ValueError("invalid_admission_kind")
        limits = [
            getattr(self.policy, kind + "_per_ten_minutes"),
            getattr(self.policy, kind + "_per_day"),
        ]
        periods = [600, 86400]
        now = int(self.clock())
        keys = [f"tsi:admission:v1:{kind}:{period}:{now // period}" for period in periods]
        digest = hashlib.sha256(str(receipt).encode()).hexdigest()
        try:
            async with asyncio.timeout(1):
                store = self.redis()
                if store is None:
                    raise ConnectionError
                allowed = await store.eval(SCRIPT, 2, *keys, digest, *limits, *periods)
        except Exception:
            raise AdmissionError(unavailable=True) from None
        if allowed != 1:
            raise AdmissionError()
