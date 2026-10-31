import asyncio
import importlib.util
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select

from app.db.demand import record_demand
from app.db.frequency import calculate_recency_and_frequency
from app.db.scheduler import (
    SchedulerBudgetDeferred,
    _candidates,
    ensure_execution_budget,
    guarded_context,
    scheduler_cycle,
)
from app.models import (
    Claim,
    Document,
    Hypothesis,
    HypothesisResearchState,
    Location,
    LocationDemandHour,
    LocationResearchState,
    ResearchJob,
    SchedulerCycle,
    SchedulerDailyBudget,
    SchedulerReservation,
)
from app.models.research_job import JobKind
from app.on_demand import request_location_research
from app.research import ResearchLeaseLost
from app.scheduling import ResourceBudget, SchedulerPolicy
from app.worker import redispatch_jobs, run_job
from tests.integration.test_intelligence_services import seed
from tests.integration.test_on_demand_research import environment as environment

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]
NOW = datetime(2030, 1, 1, 12, tzinfo=UTC)


def enable(ctx, **changes):
    ctx["settings"].scheduler = SchedulerPolicy(enabled=True, **changes)


async def place(ctx, country, name, *, demand=1, as_of=NOW, recent=False):
    async with ctx["sessions"]() as session, session.begin():
        location = Location(
            name=name,
            canonical_name=name.lower(),
            type="city",
            country_code="FJ",
            parent_id=country,
        )
        session.add(location)
        await session.flush()
        last = as_of - timedelta(hours=1) if recent else as_of - timedelta(days=20)
        session.add(
            LocationResearchState(
                location_id=location.id,
                status="ready",
                initial_completed_at=last,
                enriched_at=last,
                last_success_at=last,
                fresh_until=last + timedelta(hours=24),
                progress={"complete": True},
            )
        )
        if demand:
            session.add(
                LocationDemandHour(
                    location_id=location.id,
                    hour=as_of.replace(minute=0, second=0, microsecond=0),
                    requests=demand,
                )
            )
        return location.id


async def test_concurrent_cycles_are_one_cycle_and_daily_budget_survives_next_cycles(environment):
    ctx, country, _ = environment
    enable(
        ctx,
        cycle=ResourceBudget(jobs=1),
        daily=ResourceBudget(jobs=2, queries=36, fetches=120, llm_calls=144),
    )
    ids = [await place(ctx, country, name, demand=10) for name in ("Alpha", "Beta", "Gamma")]
    first, second = await asyncio.gather(
        scheduler_cycle(ctx, as_of=NOW), scheduler_cycle(ctx, as_of=NOW), return_exceptions=True
    )
    assert not isinstance(first, Exception) and not isinstance(second, Exception), (first, second)
    assert first["selected"][0]["target_id"] == second["selected"][0]["target_id"] == str(min(ids))
    assert first.get("replayed") or second.get("replayed")
    next_cycle = await scheduler_cycle(ctx, as_of=NOW + timedelta(minutes=15))
    assert len(next_cycle["selected"]) == 1
    exhausted = await scheduler_cycle(ctx, as_of=NOW + timedelta(minutes=30))
    assert exhausted["selected"] == []
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(ResearchJob)) == 2
        assert await session.scalar(select(func.count()).select_from(SchedulerCycle)) == 3
        assert (await session.get(SchedulerDailyBudget, NOW.date())).jobs == 2
    ctx["provider"].search.assert_not_called()
    ctx["fetcher"].fetch.assert_not_called()
    ctx["llm"].complete.assert_not_called()


async def test_demand_windows_decay_and_polling_does_not_create_queries(environment):
    ctx, location, _ = environment
    now = datetime.now(UTC)
    async with ctx["sessions"]() as session, session.begin():
        session.add(
            LocationResearchState(
                location_id=location,
                status="ready",
                initial_completed_at=now,
                enriched_at=now,
                last_success_at=now,
                fresh_until=now + timedelta(hours=1),
                progress={"complete": True},
            )
        )
        for age in (0, 0, 2, 10, 40):
            await record_demand(session, location, as_of=NOW - timedelta(days=age))
    async with ctx["sessions"]() as session:
        candidates, _ = await _candidates(session, SchedulerPolicy(), NOW)
        item = next(c for c in candidates if c["id"] == location)
        assert (item["d24"], item["d7"], item["d30"]) == (2, 3, 4)
    await request_location_research(ctx, location)
    ctx["provider"].search.assert_not_called()
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.sum(LocationDemandHour.requests))) == 6


async def test_queue_outage_retains_reservations_and_dispatch_repairs_it(environment):
    ctx, country, _ = environment
    enable(ctx)
    await place(ctx, country, "Busy", demand=20)
    ctx["redis"].enqueue_job.side_effect = ConnectionError("offline")
    result = await scheduler_cycle(ctx, as_of=NOW)
    assert result["queue_pending"] and len(result["selected"]) == 1
    ctx["redis"].enqueue_job.side_effect = None
    await redispatch_jobs(ctx)
    assert ctx["redis"].enqueue_job.call_args.args[1] == result["selected"][0]["job_id"]
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(SchedulerReservation)) == 1


async def test_utc_day_rollover_charges_once_and_blocks_without_spending(environment):
    ctx, country, _ = environment
    enable(ctx)
    await place(ctx, country, "Rollover", demand=10)
    chosen = (await scheduler_cycle(ctx, as_of=NOW))["selected"][0]
    identity = UUID(chosen["job_id"])
    tomorrow = NOW + timedelta(days=1)
    assert all(
        await asyncio.gather(
            ensure_execution_budget(ctx, identity, as_of=tomorrow),
            ensure_execution_budget(ctx, identity, as_of=tomorrow),
        )
    )
    async with ctx["sessions"]() as session:
        assert (await session.get(SchedulerDailyBudget, tomorrow.date())).jobs == 1
        assert await session.scalar(select(func.count()).select_from(SchedulerReservation)) == 2
    ctx["settings"].scheduler = ctx["settings"].scheduler.model_copy(
        update={"daily": ResourceBudget(jobs=0)}
    )
    assert not await ensure_execution_budget(ctx, identity, as_of=tomorrow + timedelta(days=1))
    async with ctx["sessions"]() as session:
        assert (await session.get(ResearchJob, identity)).attempts == 0


async def test_per_call_guard_stops_midnight_work_before_network(environment, monkeypatch):
    ctx, _, _ = environment
    monkeypatch.setattr("app.db.scheduler.ensure_execution_budget", AsyncMock(return_value=False))
    proxy = guarded_context(ctx, UUID(int=1))
    with pytest.raises(SchedulerBudgetDeferred):
        await proxy["provider"].search("Fiji scams")
    ctx["provider"].search.assert_not_called()


async def test_guard_fences_a_stale_worker_before_a_paid_call(environment):
    ctx, country, _ = environment
    enable(ctx)
    now = datetime.now(UTC)
    await place(ctx, country, "Fenced", demand=10, as_of=now)
    job_id = UUID((await scheduler_cycle(ctx, as_of=now))["selected"][0]["job_id"])
    proxy = guarded_context(ctx, job_id, token=UUID(int=1))
    with pytest.raises(ResearchLeaseLost):
        await proxy["provider"].search("Fiji scams")
    ctx["provider"].search.assert_not_called()


async def test_hypothesis_jobs_are_targeted_and_use_the_shared_worker(environment):
    ctx, country, _ = environment
    enable(ctx)
    now = datetime.now(UTC)
    async with ctx["sessions"]() as session, session.begin():
        session.add(
            LocationResearchState(
                location_id=country,
                status="ready",
                initial_completed_at=now,
                enriched_at=now,
                last_success_at=now,
                fresh_until=now + timedelta(hours=24),
                progress={"complete": True},
            )
        )
    record = await seed(ctx, country)
    async with ctx["sessions"]() as session, session.begin():
        (await session.get(Claim, record[1])).created_at = now - timedelta(hours=1)
        (await session.get(Hypothesis, record[0])).confidence_score = None
        await calculate_recency_and_frequency(session, record[0])
    result = await scheduler_cycle(ctx)
    assert len(result["selected"]) == 1 and result["selected"][0]["kind"] == "hypothesis"
    identity = result["selected"][0]["job_id"]
    await run_job(ctx, identity)
    assert ctx["provider"].search.await_count > 0
    assert "Fiji" in ctx["provider"].search.call_args.args[0]
    assert "driver demanded" in ctx["provider"].search.call_args.args[0]
    async with ctx["sessions"]() as session:
        job = await session.get(ResearchJob, UUID(identity))
        state = await session.get(HypothesisResearchState, record[0])
        assert job.kind == JobKind.HYPOTHESIS_RESEARCH and job.attempts == 1
        assert job.status in {"succeeded", "failed"} and state.last_attempt_at is not None
        assert job.result["limits"] == job.payload["scheduler_limits"]


async def test_disabled_scheduler_and_candidate_coverage_are_explicit(environment):
    ctx, country, _ = environment
    assert not (await scheduler_cycle(ctx, as_of=NOW))["enabled"]
    enable(ctx, candidate_limit=1)
    await place(ctx, country, "First")
    await place(ctx, country, "Second")
    assert not (await scheduler_cycle(ctx, as_of=NOW))["coverage_complete"]


async def test_mixed_priorities_choose_hot_first_deep_and_uncertain_before_routine(environment):
    ctx, country, _ = environment
    enable(ctx, cycle=ResourceBudget(jobs=3, fetches=180, llm_calls=216))
    hot = await place(ctx, country, "Hot", demand=20)
    deep = await place(ctx, country, "Deep")
    await place(ctx, country, "Routine")
    await place(ctx, country, "Cold", demand=0)
    focused = await place(ctx, country, "Uncertain", recent=True)
    record = await seed(ctx, focused)
    async with ctx["sessions"]() as session, session.begin():
        state = await session.get(LocationResearchState, deep)
        state.enriched_at = state.last_success_at = None
        state.progress = {"complete": False}
        claim = await session.get(Claim, record[1])
        claim.created_at = NOW - timedelta(days=1)
        hypothesis = await session.get(Hypothesis, record[0])
        hypothesis.created_at, hypothesis.confidence_score = NOW - timedelta(days=1), None
        await calculate_recency_and_frequency(session, record[0], as_of=NOW)
    result = await scheduler_cycle(ctx, as_of=NOW)
    assert {item["target_id"] for item in result["selected"]} == {
        str(hot),
        str(deep),
        str(record[0]),
    }
    assert result["resources"]["jobs"] == 3
    ctx["provider"].search.assert_not_called()


async def test_hypothesis_becoming_private_before_execution_never_calls_provider(environment):
    ctx, country, _ = environment
    enable(ctx)
    now = datetime.now(UTC)
    async with ctx["sessions"]() as session, session.begin():
        session.add(
            LocationResearchState(
                location_id=country,
                status="ready",
                initial_completed_at=now,
                enriched_at=now,
                last_success_at=now,
                fresh_until=now + timedelta(hours=24),
                progress={"complete": True},
            )
        )
    record = await seed(ctx, country)
    async with ctx["sessions"]() as session, session.begin():
        await calculate_recency_and_frequency(session, record[0])
    job_id = (await scheduler_cycle(ctx))["selected"][0]["job_id"]
    async with ctx["sessions"]() as session, session.begin():
        claim = await session.get(Claim, record[1])
        claim.structured_data = {**claim.structured_data, "sensitive": True}
    await run_job(ctx, job_id)
    ctx["provider"].search.assert_not_called()
    async with ctx["sessions"]() as session:
        assert (await session.get(ResearchJob, UUID(job_id))).result["reason"] == "not_public"


async def test_one_source_linked_to_multiple_hypotheses_is_one_location_activity(environment):
    ctx, location, _ = environment
    records = [await seed(ctx, location) for _ in range(3)]
    async with ctx["sessions"]() as session, session.begin():
        for record in records:
            (await session.get(Document, record[4])).source_id = records[0][3]
            (await session.get(Claim, record[1])).created_at = NOW - timedelta(days=1)
            await calculate_recency_and_frequency(session, record[0], as_of=NOW)
    async with ctx["sessions"]() as session:
        candidates, _ = await _candidates(session, SchedulerPolicy(), NOW)
    assert next(item for item in candidates if item["kind"] == "location")["activity"] == 1


async def test_migration_0021_round_trip_in_private_schema(environment):
    ctx, _, _ = environment
    spec = importlib.util.spec_from_file_location(
        "migration_scheduler", next(Path("migrations/versions").glob("0021_*.py"))
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def round_trip(connection):
        assert connection.scalar(select(func.current_schema())).startswith("research_test_")
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            migration.upgrade()

    async with ctx["sessions"]() as session, session.begin():
        await (await session.connection()).run_sync(round_trip)
        assert await session.scalar(select(func.count()).select_from(SchedulerCycle)) == 0
