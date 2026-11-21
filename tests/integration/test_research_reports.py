import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from mcp import Client
from sqlalchemy import func, select
from test_on_demand_research import environment as environment
from test_on_demand_research import finish_enrichment

from app.main import create_app
from app.models import Claim, LocationResearchState, ReportWatch, ResearchJob, ResearchRun
from app.on_demand import get_location_intelligence, run_location_job
from app.schemas.research_report import ResearchReport
from app.services.intelligence import IntelligenceNotFound
from app.services.research_reports import ResearchReports

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]


async def started(environment):
    ctx, location, _ = environment
    service = ResearchReports(ctx["sessions"], ctx["settings"])
    result = await service.request(ctx, location)
    return ctx, location, service, result


async def test_concurrent_requests_share_root_and_phase_membership(environment):
    ctx, location, _ = environment
    service = ResearchReports(ctx["sessions"], ctx["settings"])
    results = await asyncio.gather(*(service.request(ctx, location) for _ in range(3)))
    assert len({r["run_id"] for r in results}) == 1
    assert len({r["watch"]["id"] for r in results}) == 3
    assert all(r["latest_run"]["status"] == "queued" for r in results)
    async with ctx["sessions"]() as session:
        jobs = list(await session.scalars(select(ResearchJob)))
        assert len(jobs) == 1
        assert str(jobs[0].report_run_id) == results[0]["run_id"]
        job_id = jobs[0].id
    await run_location_job(ctx, str(job_id))
    report = await service.get(UUID(results[0]["run_id"]))
    assert report["latest_run"]["status"] == "queued" and not report["latest_run"]["terminal"]
    await finish_enrichment(ctx, location)
    terminal = await service.get(UUID(report["run_id"]))
    assert terminal["latest_run"]["status"] == "completed"
    assert terminal["retained_report"]["freshness"] == "fresh"
    assert terminal["retained_report"]["coverage"] == "complete"
    ResearchReport.model_validate(terminal)
    async with ctx["sessions"]() as session:
        assert len(set(await session.scalars(select(ResearchJob.report_run_id)))) == 1


async def test_waits_do_not_queue_or_change_research_and_revision_is_stable(environment):
    ctx, _, service, result = await started(environment)
    run, watch = UUID(result["run_id"]), UUID(result["watch"]["id"])
    before = await service.get(run)
    assert (await service.get(run))["revision"] == before["revision"]
    queue_calls = ctx["redis"].enqueue_job.await_count
    response = await service.wait(run, watch, before["revision"], wait_seconds=0)
    assert response["wait_status"] == "unchanged"
    assert response["watch"]["waits_remaining"] == 59
    assert response["revision"] == before["revision"]
    assert ctx["redis"].enqueue_job.await_count == queue_calls
    ctx["llm"].complete.assert_not_called()
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(ResearchJob)) == 1
    with pytest.raises(IntelligenceNotFound):
        await service.wait(run, uuid4(), before["revision"], wait_seconds=0)


async def test_app_watch_is_separate_and_cannot_restart_research(environment):
    ctx, _, service, result = await started(environment)
    application = create_app(ctx["settings"])
    application.state.db_sessions = ctx["sessions"]
    application.state.research_reports = service
    queue_calls = ctx["redis"].enqueue_job.await_count
    async with Client(application.state.mcp_server, mode="legacy") as client:
        response = await client.call_tool("watch_scam_report", {"run_id": result["run_id"]})
        missing = await client.call_tool("watch_scam_report", {"run_id": str(uuid4())})
    assert not response.is_error
    followed = ResearchReport.model_validate(response.structured_content)
    assert str(followed.run_id) == result["run_id"]
    assert str(followed.watch.id) != result["watch"]["id"]
    assert followed.watch.waits_remaining == 60
    assert missing.is_error and missing.structured_content["error"]["code"] == "not_found"
    assert ctx["redis"].enqueue_job.await_count == queue_calls
    ctx["llm"].complete.assert_not_called()
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(ResearchJob)) == 1
        assert await session.scalar(select(func.count()).select_from(ResearchRun)) == 1
        assert await session.scalar(select(func.count()).select_from(ReportWatch)) == 2


async def test_watch_expiry_limit_capacity_and_resume_never_restart_root(environment):
    ctx, location, service, result = await started(environment)
    run, watch = UUID(result["run_id"]), UUID(result["watch"]["id"])
    async with ctx["sessions"]() as session, session.begin():
        row = await session.get(ReportWatch, watch)
        row.waits = 60
    assert (await service.wait(run, watch, result["revision"], wait_seconds=0))[
        "wait_status"
    ] == "limit_reached"
    async with ctx["sessions"]() as session, session.begin():
        row = await session.get(ReportWatch, watch)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert (await service.wait(run, watch, result["revision"], wait_seconds=0))[
        "wait_status"
    ] == "expired"
    queues = ctx["redis"].enqueue_job.await_count
    resumed = await service.request(ctx, location, follow_run_id=run)
    assert resumed["run_id"] == str(run) and resumed["watch"]["id"] != str(watch)
    assert ctx["redis"].enqueue_job.await_count == queues
    service.active_waits = 8
    assert (
        await service.wait(run, UUID(resumed["watch"]["id"]), result["revision"], wait_seconds=0)
    )["wait_status"] == "capacity"
    assert service.active_waits == 8


async def test_partial_fresh_and_expired_are_independent_and_holds_reapply(environment):
    ctx, location, _ = environment
    await get_location_intelligence(ctx, location)
    await finish_enrichment(ctx, location)
    async with ctx["sessions"]() as session, session.begin():
        state = await session.get(LocationResearchState, location)
        run = await session.get(ResearchRun, state.current_run_id)
        identity = run.id
        run.status, run.coverage, run.stop_reasons = "partial", "partial", ["resource_limit"]
    now = datetime.now(UTC)
    service = ResearchReports(ctx["sessions"], ctx["settings"], clock=lambda: now)
    fresh = await service.get(identity)
    assert fresh["latest_run"]["status"] == "partial"
    assert fresh["retained_report"]["freshness"] == "fresh"
    service.clock = lambda: now + timedelta(seconds=ctx["settings"].research_fresh_seconds + 1)
    assert (await service.get(identity))["retained_report"]["freshness"] == "stale"
    async with ctx["sessions"]() as session, session.begin():
        claim = await session.scalar(select(Claim))
        claim.structured_data = {**claim.structured_data, "publication_status": "held"}
    assert (await service.get(identity))["items"] == []
    assert (await service.get(identity))["outcome"] == "research_incomplete"


async def test_legacy_unknown_times_and_superseded_root_are_not_rewritten(environment):
    ctx, location, service, result = await started(environment)
    identity = UUID(result["run_id"])
    async with ctx["sessions"]() as session, session.begin():
        run = await session.get(ResearchRun, identity)
        run.created_at, run.status, run.coverage = None, "partial", "unknown"
        state = await session.get(LocationResearchState, location)
        other = ResearchRun(location_id=location)
        session.add(other)
        await session.flush()
        state.current_run_id = other.id
    old = await service.get(identity)
    assert old["superseded"] and old["latest_run"]["terminal"]
    assert old["latest_run"]["created_at"] is None
    assert old["retained_report"]["freshness"] == "unknown"
    assert old["retained_report"]["last_completed_at"] is None


async def test_legacy_active_job_adoption_preserves_unknown_times_and_lease_semantics(environment):
    from app.db.jobs import create_job
    from app.models.research_job import JobKind
    from app.on_demand import _take

    ctx, location, _ = environment
    async with ctx["sessions"]() as session, session.begin():
        job = await create_job(
            session,
            kind=JobKind.LOCATION_RESEARCH,
            payload={"location_id": str(location), "phase": "initial"},
            idempotency_key=str(uuid4()),
        )
        job_id = job.id
        state = LocationResearchState(
            location_id=location,
            status="researching",
            active_job_id=job_id,
            run_token=uuid4(),
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=90),
            progress={"private_marker": "internal_only"},
        )
        session.add(state)
    service = ResearchReports(ctx["sessions"], ctx["settings"])
    result = await service.request(ctx, location)
    assert result["latest_run"]["created_at"] is None
    assert result["latest_run"]["status"] == "queued"
    assert "internal_only" not in str(result)
    await _take(ctx, job_id, uuid4())
    run_id = UUID(result["run_id"])
    current = await service.get(run_id)
    assert current["latest_run"]["status"] == "researching"
    assert current["latest_run"]["started_at"] is None
    service.clock = lambda: datetime.now(UTC) + timedelta(seconds=91)
    assert (await service.get(run_id))["latest_run"]["status"] == "queued"
    async with ctx["sessions"]() as session:
        job = await session.get(ResearchJob, job_id)
        assert job.report_run_id == run_id
        assert await session.scalar(select(func.count()).select_from(ResearchJob)) == 1
        assert (await session.get(LocationResearchState, location)).progress[
            "private_marker"
        ] == "internal_only"
    ctx["llm"].complete.assert_not_called()


async def test_report_metadata_is_fenced_against_a_superseding_root(environment):
    from app.on_demand import _owned, _take
    from app.research import ResearchLeaseLost

    ctx, location, _, _ = await started(environment)
    async with ctx["sessions"]() as session:
        state = await session.get(LocationResearchState, location)
        job_id = state.active_job_id
    token = uuid4()
    await _take(ctx, job_id, token)
    async with ctx["sessions"]() as session, session.begin():
        state = await session.get(LocationResearchState, location)
        replacement = ResearchRun(location_id=location)
        session.add(replacement)
        await session.flush()
        state.current_run_id = replacement.id
        with pytest.raises(ResearchLeaseLost):
            await _owned(session, job_id, token)


async def test_mcp_request_wait_report_contract_roundtrip(environment):
    ctx, location, _ = environment
    app = create_app(ctx["settings"])
    app.state.db_sessions, app.state.report_queue = ctx["sessions"], ctx["redis"]
    async with Client(app.state.mcp_server, mode="legacy") as client:
        result = await client.call_tool("request_scam_report", {"location_id": str(location)})
        assert not result.is_error
        data = result.structured_content
        ResearchReport.model_validate(data)
        waited = await client.call_tool(
            "wait_for_scam_report",
            dict(
                run_id=data["run_id"],
                watch_id=data["watch"]["id"],
                after_revision=data["revision"],
                wait_seconds=0,
            ),
        )
        assert not waited.is_error and waited.structured_content["wait_status"] == "unchanged"
        read = await client.call_tool("get_scam_report", {"run_id": data["run_id"]})
        assert not read.is_error and read.structured_content["revision"] == data["revision"]


async def test_completed_negative_reuses_root_and_does_not_research_on_read(environment):
    import io

    from app.integrations.fetch import FetchedPage
    from app.integrations.sarvam import Completion
    from app.relevance import Relevance

    ctx, location, _ = environment
    body = "Visitor information: museum opening hours are posted at the entrance. " * 6
    ctx["fetcher"].fetch.side_effect = lambda url: FetchedPage(url, body.encode(), "text/plain")
    ctx["storage"].get_object.side_effect = lambda **kwargs: {
        "Body": io.BytesIO(body.encode()),
        "ContentType": "text/plain",
    }
    ctx["llm"].complete.side_effect = None
    ctx["llm"].complete.return_value = Completion(
        data=Relevance(relevant=False, category=None, confidence=0.9, reason="Opening hours only"),
        requests=1,
    )
    await get_location_intelligence(ctx, location)
    await finish_enrichment(ctx, location)
    service = ResearchReports(ctx["sessions"], ctx["settings"])
    async with ctx["sessions"]() as session:
        state = await session.get(LocationResearchState, location)
        identity = state.current_run_id
        before = await session.scalar(select(func.count()).select_from(ResearchJob))
    result = await service.request(ctx, location)
    assert result["run_id"] == str(identity) and result["outcome"] == "nothing_found"
    assert result["latest_run"]["terminal"]
    assert result["retained_report"]["freshness"] == "fresh"
    calls = ctx["llm"].complete.await_count
    await service.get(identity)
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(ResearchJob)) == before
    assert ctx["llm"].complete.await_count == calls == 1


async def test_cancelled_wait_releases_capacity_and_db_connection(environment, monkeypatch):
    ctx, _, service, result = await started(environment)
    entered = asyncio.Event()
    original_get = service.get

    async def blocked(*args, **kwargs):
        value = await original_get(*args, **kwargs)
        # The repeatable-read context has exited before sleeping.
        entered.set()
        await asyncio.Event().wait()
        return value

    monkeypatch.setattr(service, "get", blocked)
    task = asyncio.create_task(
        service.wait(UUID(result["run_id"]), UUID(result["watch"]["id"]), result["revision"])
    )
    await asyncio.wait_for(entered.wait(), 3)
    assert ctx["sessions"].kw["bind"].sync_engine.pool.checkedout() == 0
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.active_waits == 0
    ctx["llm"].complete.assert_not_called()


async def test_failed_refresh_does_not_rejuvenate_retained_report(environment):
    from app.integrations.fetch import FetchError

    ctx, location, _ = environment
    await get_location_intelligence(ctx, location)
    await finish_enrichment(ctx, location)
    service = ResearchReports(ctx["sessions"], ctx["settings"])
    async with ctx["sessions"]() as session, session.begin():
        state = await session.get(LocationResearchState, location)
        old_id = state.current_run_id
        state.fresh_until = datetime.now(UTC) - timedelta(seconds=1)
    old = await service.get(old_id)
    ctx["fetcher"].fetch.side_effect = FetchError("http_status", status=503)
    latest = await service.request(ctx, location)
    async with ctx["sessions"]() as session:
        state = await session.get(LocationResearchState, location)
        job = state.active_job_id
    await run_location_job(ctx, str(job))
    read = await service.get(UUID(latest["run_id"]))
    assert read["latest_run"]["status"] in {"failed", "partial"}
    assert (
        read["items"]
        and read["retained_report"]["fresh_until"] == old["retained_report"]["fresh_until"]
    )
    assert (
        read["retained_report"]["last_completed_at"] == old["retained_report"]["last_completed_at"]
    )
    historical = await service.get(old_id)
    assert historical["superseded"]
    assert [c["id"] for c in historical["items"]] == [c["id"] for c in old["items"]]
    assert historical["retained_report"] == old["retained_report"]


async def test_withdrawn_latest_source_cannot_rejuvenate_older_evidence(environment):
    from app.discovery import SearchResult
    from app.models import Document, Source

    ctx, location, _ = environment
    await get_location_intelligence(ctx, location)
    await finish_enrichment(ctx, location)
    service = ResearchReports(ctx["sessions"], ctx["settings"])
    async with ctx["sessions"]() as session, session.begin():
        state = await session.get(LocationResearchState, location)
        run = await session.get(ResearchRun, state.current_run_id)
        old = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        run.observations = {p: {d: old for d in checks} for p, checks in run.observations.items()}
    url = f"https://example.org/{uuid4()}"
    ctx["provider"].search.return_value.results = [SearchResult(url=url, position=1, query="q")]
    latest = await service.request(ctx, location)
    async with ctx["sessions"]() as session:
        state = await session.get(LocationResearchState, location)
        job = state.active_job_id
    await run_location_job(ctx, str(job))
    identity = UUID(latest["run_id"])
    assert (await service.get(identity))["retained_report"]["freshness"] == "fresh"
    async with ctx["sessions"]() as session, session.begin():
        claim = await session.scalar(
            select(Claim).join(Document).join(Source).where(Source.canonical_url == url)
        )
        claim.structured_data = {**claim.structured_data, "publication_status": "held"}
    remaining = await service.get(identity)
    assert remaining["total"] == 1
    assert remaining["retained_report"]["freshness"] == "stale"
    assert remaining["items"][0]["freshness"] == "stale"


async def test_migration_adopts_legacy_without_fabricating_timestamps(environment):
    import importlib.util
    import json

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    ctx, location, _ = environment
    spec = importlib.util.spec_from_file_location(
        "report_migration", "migrations/versions/0022_report_runs.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def downgrade(connection):
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()

    def upgrade(connection):
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()

    engine = ctx["sessions"].kw["bind"]
    async with engine.begin() as connection:
        await connection.run_sync(downgrade)
        await connection.execute(
            text("""INSERT INTO location_research_states(location_id,status,progress)
            VALUES(:location,'stale',CAST(:progress AS jsonb))"""),
            {
                "location": location,
                "progress": json.dumps(
                    {"complete": False, "continuation": {"used": {"llm_calls": 9}}}
                ),
            },
        )
        await connection.run_sync(upgrade)
    async with ctx["sessions"]() as session:
        state = await session.get(LocationResearchState, location)
        run = await session.get(ResearchRun, state.current_run_id)
        assert run.status == "partial" and run.coverage == "partial"
        assert run.created_at is run.finished_at is run.last_completed_at is None
        assert run.observations == {} and run.stop_reasons == []
        assert state.progress["continuation"]["used"]["llm_calls"] == 9


async def test_automatic_local_mcp_loop_delivers_report_and_evidence_without_new_request(
    environment,
):
    ctx, location, _ = environment
    app = create_app(ctx["settings"])
    app.state.db_sessions, app.state.report_queue = ctx["sessions"], ctx["redis"]
    app.state.embedder = ctx["embedder"]

    async def worker():
        for _ in range(3):
            async with ctx["sessions"]() as session:
                state = await session.get(LocationResearchState, location)
                job_id = state.active_job_id
            await run_location_job(ctx, str(job_id))
            async with ctx["sessions"]() as session:
                state = await session.get(LocationResearchState, location)
                if state.status != "researching":
                    return

    async with Client(app.state.mcp_server, mode="legacy") as client:
        start = await client.call_tool("request_scam_report", {"location_id": str(location)})
        data = start.structured_content
        task = asyncio.create_task(worker())
        try:
            for _ in range(10):
                waited = await client.call_tool(
                    "wait_for_scam_report",
                    dict(
                        run_id=data["run_id"],
                        watch_id=data["watch"]["id"],
                        after_revision=data["revision"],
                    ),
                )
                assert not waited.is_error
                data = waited.structured_content
                if data["latest_run"]["terminal"]:
                    break
                await asyncio.sleep(data["next_check_after_seconds"])
            assert data["latest_run"]["terminal"] and data["total"] == 1
            evidence = await client.call_tool(
                "get_scam_evidence", {"pattern_id": data["items"][0]["id"]}
            )
            assert not evidence.is_error and evidence.structured_content["total"] == 1
            assert ctx["llm"].complete.await_count == 2
        finally:
            await task
