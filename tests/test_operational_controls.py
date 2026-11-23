import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.admission import Admission, AdmissionError
from app.core.config import Settings
from app.core.ingress import IngressMiddleware
from app.core.logging import JsonFormatter
from app.core.telemetry import ObservedClient, Telemetry
from app.main import create_app


def settings(**policy):
    return Settings(_env_file=None, environment="production", operations=policy)


async def request(app, path="/bounded", **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(path, **kwargs)


@pytest.mark.parametrize(
    "headers,body,status",
    [
        ({"Content-Length": "65537"}, b"small", 413),
        ({"Content-Encoding": "gzip"}, b"small", 415),
        ({"Content-Length": "not-a-number"}, b"small", 400),
        ({"Content-Length": "9" * 5000}, b"small", 400),
        ([("Content-Length", "1"), ("Content-Length", "1")], b"a", 400),
    ],
)
async def test_rejects_unsafe_envelopes_before_endpoint(headers, body, status):
    app = create_app(settings())
    called = False

    @app.post("/bounded")
    async def endpoint():
        nonlocal called
        called = True

    response = await request(app, headers=headers, content=body)
    assert response.status_code == status
    assert not called
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Request-ID"]


async def test_chunked_limit_and_deadline_release_capacity():
    app = create_app(settings(body_seconds=0.03))

    async def oversized():
        yield b"a" * 40000
        yield b"b" * 40000

    async def slow():
        yield b"a"
        await asyncio.sleep(0.1)
        yield b"b"

    assert (await request(app, content=oversized())).status_code == 413
    assert (await request(app, content=slow())).status_code == 408
    assert (await request(app)).status_code == 404


async def test_global_allowance_ignores_forged_forwarding_and_keeps_health_available():
    app = create_app(settings(http_per_minute=1))
    assert (await request(app)).status_code == 404
    response = await request(app, headers={"X-Forwarded-For": "8.8.8.8"})
    assert response.status_code == 429 and int(response.headers["Retry-After"]) > 0
    assert (await request(app, path="/health")).status_code != 429


async def test_inflight_and_media_limits_release_after_cancellation():
    entered, release = asyncio.Event(), asyncio.Event()

    async def inner(scope, receive, send):
        entered.set()
        await release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = IngressMiddleware(inner, settings(http_inflight=2))
    path = "/v1/reports/synthetic/attachments"
    first = asyncio.create_task(request(app, path=path))
    await entered.wait()
    assert (await request(app, path=path)).status_code == 503
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert app.active == app.media_active == 0
    release.set()
    assert (await request(app, path=path)).status_code == 200


async def test_admission_fails_closed_but_metrics_fail_open():
    guard = Admission(settings(), lambda: None)
    with pytest.raises(AdmissionError) as error:
        await guard.require("reports", "synthetic")
    assert error.value.status == 503
    metrics = Telemetry(lambda: None)
    assert await metrics.record("provider.fetch", error=True) is False
    assert (await metrics.snapshot())["values"] is None
    with pytest.raises(ValueError):
        await metrics.record("raw-client-controlled-label")
    await Admission(Settings(_env_file=None, environment="test"), lambda: None).require(
        "reports", "x"
    )


async def test_provider_observer_preserves_results_and_unknown_usage():
    metrics = SimpleNamespace(record=AsyncMock())
    result = SimpleNamespace(requests=1, usage={}, error="timeout")
    client = SimpleNamespace(model="synthetic", complete=AsyncMock(return_value=result))
    observed = ObservedClient(client, metrics, "sarvam")
    assert observed.model == "synthetic"
    assert await observed.complete(system="private", user="secret", schema=None) is result
    fields = metrics.record.await_args.kwargs
    assert fields["usage_unknown"] == 1 and "tokens" not in fields
    assert fields["error"] is True and "private" not in str(fields)


def test_log_formatter_does_not_format_arbitrary_message_arguments_or_exceptions():
    record = logging.LogRecord(
        "app.test", logging.ERROR, "", 1, "token=%s", ("private-value",), None
    )
    record.path = "/unknown?token=private-value"
    record.job_id = "private-value"
    record.error_type = "ValueError"
    formatted = JsonFormatter().format(record)
    assert "private-value" not in formatted
    assert json.loads(formatted)["message"] == "application_event"


async def test_unknown_paths_are_not_logged(capsys):
    app = create_app(Settings(_env_file=None))
    await request(app, path="/private-secret-value")
    logs = capsys.readouterr().err
    assert "private-secret-value" not in logs
    assert "/<unmatched>" in logs
