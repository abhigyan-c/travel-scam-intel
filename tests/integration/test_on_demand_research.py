import asyncio
import io
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.session import Base, create_database_engine
from app.discovery import SearchResponse, SearchResult, SearchUsage
from app.embeddings import MODEL, REVISION
from app.extraction import ClaimBatch, ExtractedClaim
from app.hypothesis_resolution import Decision
from app.integrations.fetch import FetchedPage
from app.integrations.sarvam import Completion
from app.models import (
    Claim,
    Document,
    Evidence,
    Hypothesis,
    HypothesisResolution,
    Location,
    LocationResearchState,
    ResearchJob,
)
from app.on_demand import (
    _owned,
    _take,
    get_location_intelligence,
    read_location_intelligence,
    run_location_job,
)
from app.relevance import Relevance
from app.research import ResearchLeaseLost
from app.worker import run_job

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]
BODY = (
    "I was in Fiji when a driver demanded extra money after I agreed to a fixed fare "
    "on 2026-01-02T10:00:00+00:00."
)


@pytest.fixture
async def environment():
    settings = Settings(research_max_search_queries=1, research_retry_seconds=1)
    admin = create_database_engine(settings)
    schema = "research_test_" + uuid4().hex
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        connect_args={"options": f"-csearch_path={schema},public"},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda connection: Base.metadata.create_all(connection, checkfirst=False)
            )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session, session.begin():
            location = Location(
                name="Fiji", canonical_name="fiji", type="country", country_code="FJ"
            )
            session.add(location)
            await session.flush()
            location_id = location.id
        provider, fetcher, llm, redis = AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
        provider.search.return_value = SearchResponse(
            results=[
                SearchResult(url=f"https://example.org/{uuid4()}", position=1, query="Fiji scams")
            ],
            usage=SearchUsage(returned_results=1),
        )
        fetcher.fetch.side_effect = lambda url: FetchedPage(url, BODY.encode(), "text/plain")
        storage = MagicMock()
        storage.get_object.side_effect = lambda **kwargs: {
            "Body": io.BytesIO(BODY.encode()),
            "ContentType": "text/plain",
        }
        claim = ExtractedClaim(
            claim="A traveler reports a driver demanded extra money after a fixed fare agreement.",
            evidence_quote=BODY,
            location="Fiji",
            approach=["agreed to a fixed fare"],
            methods=["demanded extra money"],
            warning_signs=[],
            entities=[],
            occurred_at="2026-01-02T10:00:00+00:00",
            first_hand=True,
            confidence=0.9,
            sensitive=False,
        )

        async def complete(**kwargs):
            if kwargs["schema"] is Relevance:
                return Completion(
                    data=Relevance(
                        relevant=True, category="fare", confidence=0.9, reason="Fare report"
                    ),
                    requests=1,
                )
            if kwargs["schema"] is Decision:
                return Completion(
                    data=Decision(
                        decision="NEW",
                        target_id=None,
                        confidence=0.95,
                        ambiguous=False,
                        reason="Different reported pattern",
                    ),
                    requests=1,
                )
            return Completion(data=ClaimBatch(claims=[claim]), requests=1)

        llm.complete.side_effect = complete
        embedder = MagicMock(model=MODEL, revision=REVISION)
        embedder.preload = AsyncMock()
        embedder.embed = AsyncMock(return_value=[[1.0] + [0.0] * 383])
        ctx = {
            "settings": settings,
            "sessions": sessions,
            "provider": provider,
            "fetcher": fetcher,
            "llm": llm,
            "redis": redis,
            "storage": storage,
            "bucket": "fixture",
            "embedder": embedder,
        }
        yield ctx, location_id, claim
    finally:
        await engine.dispose()
        # Only this fixture's generated schema is removed; public/user data is untouched.
        assert schema.startswith("research_test_") and len(schema) == 46
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def finish_enrichment(ctx, location_id):
    result = await read_location_intelligence(ctx, location_id)
    await run_job(ctx, str(result.active_job_id))
    return await read_location_intelligence(ctx, location_id)


async def test_unseen_location_produces_persisted_scored_initial_cards_then_enriches(environment):
    ctx, identity, _ = environment
    initial = await get_location_intelligence(ctx, identity)
    assert initial.initial and not initial.enriched and initial.in_progress
    assert len(initial.hypotheses) == 1 and initial.hypotheses[0]["confidence_score"] > 0
    assert initial.hypotheses[0]["status"] == "candidate"
    final = await finish_enrichment(ctx, identity)
    assert final.status == "ready" and final.enriched and final.complete
    assert final.hypotheses == initial.hypotheses
    calls = ctx["provider"].search.await_count
    cached = await get_location_intelligence(ctx, identity)
    assert cached.hypotheses == final.hypotheses and ctx["provider"].search.await_count == calls
    assert ctx["llm"].complete.await_count == 2  # Relevance/extraction are cached in continuation.
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(Claim)) == 1
        assert await session.scalar(select(func.count()).select_from(Evidence)) == 1


async def test_successful_empty_becomes_ready_and_does_not_invent_findings(environment):
    ctx, identity, _ = environment
    ctx["provider"].search.return_value.results = []
    first = await get_location_intelligence(ctx, identity)
    assert first.initial and first.hypotheses == []
    final = await finish_enrichment(ctx, identity)
    assert final.status == "ready" and final.complete and final.message == "Nothing was found."
    ctx["llm"].complete.assert_not_called()


async def test_provider_failure_is_not_successful_empty_and_retry_is_throttled(environment):
    ctx, identity, _ = environment
    ctx["provider"].search.return_value = SearchResponse(usage=SearchUsage(error="rate_limited"))
    await get_location_intelligence(ctx, identity)
    final = await finish_enrichment(ctx, identity)
    assert final.status == "failed" and not final.complete
    assert final.message != "Nothing was found."
    calls = ctx["provider"].search.await_count
    await get_location_intelligence(ctx, identity)
    assert ctx["provider"].search.await_count == calls


async def test_simultaneous_first_queries_share_one_run_and_can_read_progress(environment):
    ctx, identity, _ = environment
    entered, release = asyncio.Event(), asyncio.Event()
    response = ctx["provider"].search.return_value

    async def search(query):
        entered.set()
        await release.wait()
        return response

    ctx["provider"].search.side_effect = search
    first = asyncio.create_task(get_location_intelligence(ctx, identity))
    await asyncio.wait_for(entered.wait(), 5)
    others = await asyncio.gather(*(get_location_intelligence(ctx, identity) for _ in range(4)))
    assert all(row.in_progress and not row.initial for row in others)
    assert len({row.active_job_id for row in others}) == 1
    assert ctx["provider"].search.await_count == 1
    release.set()
    await first


async def test_stale_query_returns_cards_and_reserves_only_one_background_refresh(environment):
    ctx, identity, _ = environment
    await get_location_intelligence(ctx, identity)
    final = await finish_enrichment(ctx, identity)
    async with ctx["sessions"]() as session, session.begin():
        state = await session.get(LocationResearchState, identity)
        state.fresh_until = datetime.now(UTC) - timedelta(seconds=1)
    calls = ctx["provider"].search.await_count
    one, two = await asyncio.gather(
        get_location_intelligence(ctx, identity), get_location_intelligence(ctx, identity)
    )
    assert one.stale and one.in_progress and one.hypotheses == final.hypotheses
    assert one.active_job_id == two.active_job_id
    assert ctx["provider"].search.await_count == calls


async def test_deadline_commits_first_document_then_background_continues(environment):
    ctx, identity, _ = environment
    ctx["settings"] = ctx["settings"].model_copy(update={"research_max_total_seconds": 1})
    first_url = ctx["provider"].search.return_value.results[0].url
    ctx["provider"].search.return_value.results.append(
        SearchResult(url=first_url + "/slow", position=2, query="q")
    )

    async def fetch(url):
        if url.endswith("/slow"):
            await asyncio.sleep(3)
        return FetchedPage(url, BODY.encode(), "text/plain")

    ctx["fetcher"].fetch.side_effect = fetch
    result = await get_location_intelligence(ctx, identity)
    assert result.initial and result.in_progress and not result.complete
    assert len(result.hypotheses) == 1
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(Claim)) == 1
        # The initial job is retained separately from the queued enrichment job.
        initial_job = await session.scalar(
            select(ResearchJob).where(ResearchJob.payload["phase"].astext == "initial")
        )
        assert initial_job.result["diagnostics"]["stage_timings"]
    ctx["settings"] = ctx["settings"].model_copy(update={"research_max_total_seconds": 5})
    ctx["fetcher"].fetch.side_effect = lambda url: FetchedPage(url, BODY.encode(), "text/plain")
    final = await finish_enrichment(ctx, identity)
    assert final.enriched and len(final.hypotheses) == 1


async def test_expired_worker_is_fenced_and_redelivery_reuses_the_job(environment):
    ctx, identity, _ = environment
    reserved = await get_location_intelligence(ctx, identity, wait_for_initial=False)
    old, new = uuid4(), uuid4()
    await _take(ctx, reserved.active_job_id, old)
    async with ctx["sessions"]() as session, session.begin():
        job = await session.get(ResearchJob, reserved.active_job_id)
        state = await session.get(LocationResearchState, identity)
        job.lease_expires_at = state.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await _take(ctx, reserved.active_job_id, new)
    async with ctx["sessions"]() as session:
        with pytest.raises(ResearchLeaseLost):
            await _owned(session, reserved.active_job_id, old)
    async with ctx["sessions"]() as session, session.begin():
        job = await session.get(ResearchJob, reserved.active_job_id)
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await run_location_job(ctx, str(reserved.active_job_id))
    assert (await read_location_intelligence(ctx, identity)).initial


async def test_outer_deadline_keeps_safe_pipeline_diagnostics(environment, monkeypatch):
    ctx, identity, _ = environment
    ctx["settings"] = ctx["settings"].model_copy(update={"research_max_total_seconds": 0.1})

    async def stalled(*args, diagnostics, **kwargs):
        diagnostics.update(active_stage="extraction", stage_timings=[], llm_calls=2)
        await asyncio.sleep(2)

    monkeypatch.setattr("app.on_demand.research_location", stalled)
    await get_location_intelligence(ctx, identity)
    async with ctx["sessions"]() as session:
        job = await session.scalar(
            select(ResearchJob).where(ResearchJob.payload["phase"].astext == "initial")
        )
        assert job.result["error_code"] == "deadline" and not job.result["complete"]
        assert job.result["diagnostics"]["active_stage"] == "extraction"
        assert job.result["diagnostics"]["llm_calls"] == 2


async def test_redis_outage_leaves_a_committed_dispatchable_job(environment):
    ctx, identity, _ = environment
    ctx["redis"].enqueue_job.side_effect = ConnectionError("fixture")
    result = await get_location_intelligence(ctx, identity, wait_for_initial=False)
    assert result.queue_pending and result.in_progress
    async with ctx["sessions"]() as session:
        assert (await session.get(ResearchJob, result.active_job_id)).status.value == "queued"
    ctx["redis"].enqueue_job.side_effect = None
    await run_job(ctx, str(result.active_job_id))
    assert (await read_location_intelligence(ctx, identity)).initial


async def test_sensitive_claim_is_retained_for_review_and_not_published(environment):
    ctx, identity, claim = environment
    claim.sensitive = True
    result = await get_location_intelligence(ctx, identity)
    assert result.hypotheses == [] and not result.complete
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(Claim)) == 1
        assert await session.scalar(select(func.count()).select_from(Hypothesis)) == 0
        resolution = await session.scalar(select(HypothesisResolution))
        assert resolution.needs_review


async def test_semantic_resolution_stays_pending_without_synthetic_operator_approval(environment):
    ctx, identity, claim = environment
    await get_location_intelligence(ctx, identity)
    # A new extraction version proposes a different pattern in the existing scope.
    claim.claim = "A traveler reports a separate fare negotiation problem."
    # A new URL/raw document gives a second ordinary claim without changing the first.
    ctx["provider"].search.return_value.results = [
        SearchResult(url=f"https://example.org/{uuid4()}", position=1, query="q")
    ]
    final = await finish_enrichment(ctx, identity)
    assert not final.complete and len(final.hypotheses) == 1
    async with ctx["sessions"]() as session:
        decisions = (
            await session.scalars(
                select(HypothesisResolution).where(HypothesisResolution.needs_review.is_(True))
            )
        ).all()
        assert any(
            "semantic_decision_requires_review" in row.details["review_reasons"]
            for row in decisions
        )
        assert await session.scalar(select(func.count()).select_from(Hypothesis)) == 1


async def test_cancellation_retains_committed_stage_and_retry_continues(environment):
    ctx, identity, _ = environment
    entered = asyncio.Event()
    original = ctx["llm"].complete.side_effect

    async def complete(**kwargs):
        if kwargs["schema"] is Relevance:
            entered.set()
            await asyncio.Event().wait()
        return await original(**kwargs)

    ctx["llm"].complete.side_effect = complete
    task = asyncio.create_task(get_location_intelligence(ctx, identity))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    current = await read_location_intelligence(ctx, identity)
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(Document)) == 1
        job = await session.get(ResearchJob, current.active_job_id)
        assert job.status.value == "retry"
    ctx["llm"].complete.side_effect = original
    await run_job(ctx, str(current.active_job_id))
    assert len((await read_location_intelligence(ctx, identity)).hypotheses) == 1


async def test_existing_cards_do_not_bypass_a_new_sensitivity_hold(environment):
    ctx, identity, _ = environment
    result = await get_location_intelligence(ctx, identity)
    assert result.hypotheses
    async with ctx["sessions"]() as session, session.begin():
        claim = await session.scalar(select(Claim))
        claim.structured_data = {**claim.structured_data, "sensitive": True}
    assert (await read_location_intelligence(ctx, identity)).hypotheses == []


async def test_read_without_request_is_non_mutating_and_missing_location_is_rejected(environment):
    ctx, identity, _ = environment
    result = await read_location_intelligence(ctx, identity)
    assert result.status == "never_researched" and not result.in_progress
    ctx["provider"].search.assert_not_called()
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(ResearchJob)) == 0
    with pytest.raises(ValueError):
        await get_location_intelligence(ctx, uuid4())


async def test_embedding_deadline_does_not_rollback_completed_scored_hypothesis(environment):
    ctx, identity, _ = environment
    ctx["settings"] = ctx["settings"].model_copy(update={"research_max_total_seconds": 1})

    async def slow_model_load():
        await asyncio.sleep(3)

    ctx["embedder"].preload.side_effect = slow_model_load
    result = await get_location_intelligence(ctx, identity)
    assert result.initial and not result.complete and len(result.hypotheses) == 1
    assert result.hypotheses[0]["confidence_score"] > 0


async def test_periodic_dispatch_replays_committed_location_job_after_queue_outage(environment):
    from app.worker import redispatch_jobs

    ctx, identity, _ = environment
    ctx["redis"].enqueue_job.side_effect = ConnectionError("fixture")
    pending = await get_location_intelligence(ctx, identity, wait_for_initial=False)
    assert pending.queue_pending
    ctx["redis"].enqueue_job.side_effect = None
    ctx["redis"].enqueue_job.reset_mock()
    await redispatch_jobs(ctx)
    ctx["redis"].enqueue_job.assert_awaited_once()
    assert ctx["redis"].enqueue_job.await_args.args == ("run_job", str(pending.active_job_id))


async def test_repeated_crashes_exhaust_attempts_without_leaving_a_permanent_lease(environment):
    ctx, identity, _ = environment
    pending = await get_location_intelligence(ctx, identity, wait_for_initial=False)
    for _ in range(3):
        assert await _take(ctx, pending.active_job_id, uuid4())
        async with ctx["sessions"]() as session, session.begin():
            job = await session.get(ResearchJob, pending.active_job_id)
            job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await _take(ctx, pending.active_job_id, uuid4()) is None
    result = await read_location_intelligence(ctx, identity)
    assert result.status == "failed" and not result.in_progress
    async with ctx["sessions"]() as session:
        state = await session.get(LocationResearchState, identity)
        assert state.run_token is state.lease_expires_at is None
        assert state.next_retry_at is not None
