import asyncio

import httpx
import pytest

from app.core import readiness
from app.core.config import Settings
from app.main import create_app


@pytest.mark.parametrize("failed", [None, "postgres", "redis", "storage"])
async def test_readiness_status_and_liveness_are_separate(monkeypatch, failed):
    async def database(engine):
        return failed != "postgres"

    async def redis(client):
        if failed == "redis":
            raise ConnectionError("redis://private-password@internal-host")
        return True

    async def storage(client, bucket):
        return failed != "storage"

    monkeypatch.setattr(readiness, "check_database", database)
    monkeypatch.setattr(readiness, "check_redis", redis)
    monkeypatch.setattr(readiness, "check_storage", storage)
    application = create_app(Settings(_env_file=None))
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client,
    ):
        response = await client.get("/ready", headers={"X-Request-ID": "readiness-test"})
        health = await client.get("/health")
    assert response.status_code == (503 if failed else 200)
    assert response.json() == {
        "status": "not_ready" if failed else "ready",
        "checks": {
            name: "unavailable" if name == failed else "ok"
            for name in ("postgres", "redis", "storage")
        },
    }
    assert response.headers["X-Request-ID"] == "readiness-test"
    assert "private-password" not in response.text
    assert health.status_code == 200


async def test_checks_run_together_and_timeout_without_leaking_errors(monkeypatch):
    started = set()
    all_started = asyncio.Event()

    async def probe(name):
        started.add(name)
        if len(started) == 3:
            all_started.set()
        await all_started.wait()
        if name == "storage":
            await asyncio.sleep(10)
        return True

    monkeypatch.setattr(readiness, "check_database", lambda _: probe("postgres"))
    monkeypatch.setattr(readiness, "check_redis", lambda _: probe("redis"))
    monkeypatch.setattr(readiness, "check_storage", lambda *_: probe("storage"))
    result = await asyncio.wait_for(
        readiness.check_dependencies(None, None, None, "raw", 0.05), timeout=1
    )
    assert started == {"postgres", "redis", "storage"}
    assert result == {"postgres": "ok", "redis": "ok", "storage": "unavailable"}


async def test_probe_cancellation_is_not_swallowed():
    async def cancelled():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await readiness.run_check(cancelled(), timeout=1)
