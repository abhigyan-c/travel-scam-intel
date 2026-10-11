import asyncio
import json
from uuid import UUID

import httpx
import pytest

from app.core.config import Settings
from app.core.logging import request_id
from app.main import create_app


@pytest.mark.parametrize("supplied", ["trace-123_abc.xyz", "a", "x" * 64])
async def test_valid_request_id_is_preserved(supplied):
    application = create_app(Settings(_env_file=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.get("/health", headers={"X-Request-ID": supplied})
    assert response.headers["X-Request-ID"] == supplied


@pytest.mark.parametrize("supplied", ["", "x" * 65, "contains space", "<script>", ".prefix"])
async def test_invalid_request_id_is_replaced(supplied):
    application = create_app(Settings(_env_file=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.get("/health", headers={"X-Request-ID": supplied})
    assert str(UUID(response.headers["X-Request-ID"])) == response.headers["X-Request-ID"]


async def test_concurrent_request_context_is_isolated_and_reset():
    application = create_app(Settings(_env_file=None))
    both_started = asyncio.Event()
    arrivals = 0

    @application.get("/context")
    async def context():
        nonlocal arrivals
        before = request_id.get()
        arrivals += 1
        if arrivals == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)
        return {"before": before, "after": request_id.get()}

    assert request_id.get() is None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        responses = await asyncio.gather(
            client.get("/context", headers={"X-Request-ID": "first"}),
            client.get("/context", headers={"X-Request-ID": "second"}),
        )
        await client.get("/health")
        assert request_id.get() is None
    for response, expected in zip(responses, ("first", "second"), strict=True):
        assert response.json() == {"before": expected, "after": expected}
        assert response.headers["X-Request-ID"] == expected


async def test_request_logs_are_json_and_omit_sensitive_input(capsys):
    application = create_app(Settings(_env_file=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.get(
            "/health?token=query-secret",
            headers={"X-Request-ID": "log-request", "Authorization": "Bearer header-secret"},
        )
    logs = capsys.readouterr().err
    assert "query-secret" not in logs
    assert "header-secret" not in logs
    record = json.loads(logs.strip())
    assert record["message"] == "request_completed"
    assert record["request_id"] == response.headers["X-Request-ID"] == "log-request"
    assert record["method"] == "GET"
    assert record["path"] == "/health"
    assert record["status"] == 200
    assert record["duration_ms"] >= 0
