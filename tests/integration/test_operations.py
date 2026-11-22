import asyncio
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select
from test_on_demand_research import environment as environment

from app.api.locations import get_session
from app.core.admission import Admission, AdmissionError
from app.core.config import Settings
from app.core.telemetry import ObservedClient, Telemetry
from app.main import create_app
from app.models import ResearchJob, UserReport
from app.on_demand import get_location_intelligence, request_location_research
from app.schemas.report import ReportCreate
from app.services.report_intake import submit_report
from app.services.research_reports import ResearchReports

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]


@pytest.fixture
async def store():
    redis = Redis.from_url(Settings().redis_url.get_secret_value())
    prefix = "pr37-test:" + uuid4().hex + ":"

    class Isolated:
        async def eval(self, script, number, *args):
            return await redis.eval(
                script, number, *[prefix + key for key in args[:number]], *args[number:]
            )

        def pipeline(self, **kwargs):
            return redis.pipeline(**kwargs)

        async def hgetall(self, key):
            return await redis.hgetall(key)

    try:
        yield redis, Isolated(), prefix
    finally:
        keys = [key async for key in redis.scan_iter(prefix + "*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


async def test_atomic_shared_quota_replay_windows_and_no_growth_on_rejection(store):
    redis, scoped, prefix = store
    config = Settings(
        _env_file=None,
        environment="production",
        operations={"reports_per_ten_minutes": 2, "reports_per_day": 3},
    )
    clock = [86400]
    # Separate service instances use the same shared quota.
    a, b = [Admission(config, lambda: scoped, clock=lambda: clock[0]) for _ in range(2)]
    results = await asyncio.gather(
        *[guard.require("reports", f"receipt-{i}") for i, guard in enumerate([a, b, a, b, a, b])],
        return_exceptions=True,
    )
    assert sum(value is None for value in results) == 2
    assert all(value is None or isinstance(value, AdmissionError) for value in results)
    admitted = [i for i, value in enumerate(results) if value is None]
    await b.require("reports", f"receipt-{admitted[0]}")
    for key in [key async for key in redis.scan_iter(prefix + "*")]:
        assert await redis.hlen(key) == 3  # count plus two admitted receipts
        assert 0 < await redis.ttl(key) <= 86401
    clock[0] += 600
    await a.require("reports", "new-window")
    with pytest.raises(AdmissionError):
        await b.require("reports", "day-limit")
    # Reads/writes have separate budgets; watch creation cannot consume report credit.
    await a.require("watches", "watch")
    clock[0] += 86400
    await b.require("reports", "next-day")


async def test_redis_numeric_snapshot_and_expiry(store):
    redis, _, prefix = store
    metrics = Telemetry(lambda: redis, clock=lambda: datetime(2026, 11, 22, tzinfo=UTC))
    metrics.key = lambda: prefix + "metrics"
    await metrics.record("provider.fetch", milliseconds=700, error=True)
    await metrics.record("provider.fetch", milliseconds=10)
    result = await metrics.snapshot()
    assert result["values"]["provider.fetch.count"] == 2
    assert result["values"]["provider.fetch.errors"] == 1
    assert result["values"]["provider.fetch.le_2000"] == 1
    assert result["values"]["provider.fetch.le_100"] == 1
    assert 0 < await redis.ttl(metrics.key()) <= 172800


async def test_operations_requires_admin_and_has_no_raw_payloads(session):
    token = "synthetic-admin-" + "x" * 32
    app = create_app(Settings(_env_file=None, environment="test", admin_api_token=token))

    async def override():
        yield session

    app.dependency_overrides[get_session] = override
    app.state.telemetry = type(
        "Metrics", (), {"snapshot": AsyncMock(return_value={"available": False})}
    )()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/v1/admin/operations")).status_code == 401
        response = await client.get(
            "/v1/admin/operations", headers={"Authorization": "Bearer " + token}
        )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    data = response.json()
    assert data["research_sample"]["limit"] == 200
    assert "reports_moderation" in data["persisted_counts"]
    assert not any(
        key in response.text for key in ("report_text", "raw_object_path", "canonical_url")
    )


async def test_report_receipt_replay_bypasses_exhausted_and_unavailable_admission(
    environment, store
):
    ctx, location, _ = environment
    _, scoped, _ = store
    guard = Admission(
        Settings(
            _env_file=None, environment="production", operations={"reports_per_ten_minutes": 1}
        ),
        lambda: scoped,
    )
    payload = ReportCreate(
        report_text=(
            "Synthetic test: I agreed to a fixed fare for a taxi ride, "
            "but the driver demanded extra money after arriving. "
            "This is only a test of the reporting system."
        ),
        location_id=location,
        provenance_class="SECOND_HAND",
    )
    key = str(uuid4())

    async def submit(key, guard):
        async with ctx["sessions"]() as session:
            return await submit_report(
                session,
                payload,
                key,
                storage=ctx["storage"],
                bucket=ctx["bucket"],
                queue=ctx["redis"],
                admission=guard,
            )

    first = await submit(key, guard)
    unavailable = Admission(Settings(_env_file=None, environment="production"), lambda: None)
    repeated = await submit(key, unavailable)
    assert repeated.replayed and repeated.report_id == first.report_id
    with pytest.raises(AdmissionError) as error:
        await submit(str(uuid4()), guard)
    assert error.value.status == 429
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(UserReport)) == 1
        assert await session.scalar(select(func.count()).select_from(ResearchJob)) == 1
    assert ctx["storage"].put_object.call_count == 1
    ctx["llm"].complete.assert_not_called()


async def test_new_root_rejected_before_job_but_shared_root_and_polling_stay_free(environment):
    ctx, location, _ = environment
    guard = type("Guard", (), {"require": AsyncMock(side_effect=AdmissionError())})()
    ctx = {**ctx, "admission": guard}
    with pytest.raises(AdmissionError):
        await request_location_research(ctx, location)
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(ResearchJob)) == 0
    guard.require.side_effect = None
    guard.require.reset_mock()
    service = ResearchReports(ctx["sessions"], ctx["settings"], admission=guard)
    first = await service.request(ctx, location)
    second = await service.request(ctx, location)
    assert first["run_id"] == second["run_id"]
    assert [call.args[0] for call in guard.require.await_args_list] == [
        "research",
        "watches",
        "watches",
    ]
    from uuid import UUID

    await service.get(UUID(first["run_id"]))
    await service.wait(
        UUID(first["run_id"]), UUID(first["watch"]["id"]), first["revision"], wait_seconds=0
    )
    assert guard.require.await_count == 3


async def test_observed_provider_clients_preserve_research_and_publication(environment):
    ctx, location, _ = environment
    metrics = type("Metrics", (), {"record": AsyncMock()})()
    for name, kind in (
        ("provider", "search"),
        ("fetcher", "fetch"),
        ("llm", "sarvam"),
        ("embedder", "embedding"),
    ):
        ctx[name] = ObservedClient(ctx[name], metrics, kind)
    result = await get_location_intelligence(ctx, location)
    assert len(result.hypotheses) == 1
    names = {call.args[0] for call in metrics.record.await_args_list}
    assert {"provider.search", "provider.fetch", "provider.sarvam", "provider.embedding"} <= names
