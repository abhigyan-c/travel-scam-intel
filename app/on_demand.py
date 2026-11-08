"""Durable location leases and bounded reuse of the existing research pipeline.

This service owns short-lived sessions/commits. It is not an HTTP response contract.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import Settings
from app.db.claim_pipeline import apply_and_score_claim, embed_hypothesis
from app.db.demand import record_demand
from app.db.jobs import create_job, dispatch_pending
from app.db.resolution import _descendants, get_location_path
from app.discovery import generate_queries
from app.models import Claim, Evidence, Hypothesis, LocationResearchState, ResearchJob, UserReport
from app.models.hypothesis import HypothesisStatus
from app.models.research_job import JobKind, JobStatus
from app.research import ResearchLeaseLost, ResearchLimits, research_location
from app.research_progress import Cursor, Progress

LEASE_SECONDS = 90


class LocationIntelligence(BaseModel):
    location_id: UUID
    status: str
    initial: bool
    enriched: bool
    stale: bool
    in_progress: bool
    complete: bool
    active_job_id: UUID | None
    queue_pending: bool
    message: str
    hypotheses: list[dict]
    progress: dict


def limits_for(settings, phase):
    multiplier = 1 if phase == "initial" else 2
    return ResearchLimits(
        queries=min(6, settings.research_max_search_queries * multiplier),
        candidates=min(100, settings.research_max_search_results * multiplier),
        fetches=min(20, settings.research_max_documents_to_fetch * multiplier),
        documents=min(10, settings.research_max_relevant_documents * multiplier),
        llm_calls=min(24, settings.research_max_llm_calls * multiplier),
        llm_concurrency=settings.research_max_llm_concurrency,
        timeout=min(45, settings.research_max_total_seconds * multiplier),
        fetch_timeout=settings.research_max_fetch_seconds,
    )


def search_label(path):
    label = ", ".join(row.name for row in reversed(path))
    generate_queries(label)
    return label


async def _new_job(session, state, phase, *, scheduler_limits=None):
    if phase in {"initial", "refresh"} or scheduler_limits is not None:
        state.progress = {}
    generation = uuid4()
    job = await create_job(
        session,
        kind=JobKind.LOCATION_RESEARCH,
        payload={
            "location_id": str(state.location_id),
            "phase": phase,
            **({"scheduler_limits": scheduler_limits.model_dump()} if scheduler_limits else {}),
        },
        idempotency_key=f"location:{state.location_id}:{generation}",
    )
    state.status, state.active_job_id = "researching", job.id
    state.run_token = generation  # Reservation until the worker obtains its own fenced token.
    state.lease_expires_at = datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)
    return job


async def _reserve(ctx, location_id):
    async with ctx["sessions"]() as session, session.begin():
        await get_location_path(session, location_id)  # Validate identity and parent graph.
        await record_demand(session, location_id)
        await session.execute(
            insert(LocationResearchState).values(location_id=location_id).on_conflict_do_nothing()
        )
        state = await session.scalar(
            select(LocationResearchState)
            .where(LocationResearchState.location_id == location_id)
            .with_for_update()
        )
        now = datetime.now(UTC)
        if state.status == "researching":
            return state.active_job_id, False
        if state.status == "ready" and state.fresh_until is not None and state.fresh_until > now:
            return None, False
        if state.next_retry_at is not None and state.next_retry_at > now:
            return None, False
        phase = "initial" if state.initial_completed_at is None else "refresh"
        job = await _new_job(session, state, phase)
        return job.id, phase == "initial"


async def _dispatch(ctx, job_id):
    if job_id is None:
        return False
    try:
        async with ctx["sessions"]() as session:
            await dispatch_pending(session, ctx["redis"], job_ids=[job_id])
        return False
    except Exception:
        # The committed database job is the outbox; periodic dispatch repairs Redis outages.
        return True


async def request_location_research(ctx, location_id: UUID) -> bool:
    """Reserve/dispatch shared research without running providers or reading internal cards.

    Returns whether dispatch is pending; the durable outbox survives queue outages.
    """
    job_id, _ = await _reserve(ctx, location_id)
    return await _dispatch(ctx, job_id)


async def get_location_intelligence(ctx, location_id: UUID, *, wait_for_initial: bool = True):
    job_id, initial = await _reserve(ctx, location_id)
    if initial and wait_for_initial:
        await run_location_job(ctx, str(job_id))
        async with ctx["sessions"]() as session:
            state = await session.get(LocationResearchState, location_id)
            job_id = state.active_job_id if state.status == "researching" else None
    queue_pending = await _dispatch(ctx, job_id)
    return await read_location_intelligence(ctx, location_id, queue_pending=queue_pending)


async def read_location_intelligence(ctx, location_id, *, queue_pending=False):
    async with ctx["sessions"]() as session:
        await get_location_path(session, location_id)
        state = await session.get(LocationResearchState, location_id)
        if state is None:
            return LocationIntelligence(
                location_id=location_id,
                status="never_researched",
                initial=False,
                enriched=False,
                stale=False,
                in_progress=False,
                complete=False,
                active_job_id=None,
                queue_pending=False,
                message="This location has not been researched.",
                hypotheses=[],
                progress={},
            )
        # PR 27 will define the full public evidence contract; expose only eligible persisted cards.
        eligible = (
            select(Evidence.id)
            .join(Claim, Evidence.claim_id == Claim.id)
            .where(
                Evidence.hypothesis_id == Hypothesis.id,
                Evidence.archived_at.is_(None),
                Evidence.stance == "supports",
                Evidence.weight_run_id.is_not(None),
                Evidence.weight_components["public_eligible"].as_boolean().is_(True),
                Claim.location_id == Hypothesis.primary_location_id,
                Claim.structured_data["publication_status"].astext.in_(["unreviewed", "approved"]),
                Claim.structured_data["sensitive"].astext.is_not_distinct_from("false"),
                Claim.structured_data["entities"].is_(None)
                | (Claim.structured_data["entities"] == []),
                ~select(UserReport.id)
                .where(
                    UserReport.document_id == Claim.document_id,
                    UserReport.moderation_status.not_in(["processed", "accepted"]),
                )
                .correlate(Claim)
                .exists(),
            )
            .exists()
        )
        rows = (
            await session.scalars(
                select(Hypothesis)
                .where(
                    Hypothesis.primary_location_id.in_(select(_descendants(location_id).c.id)),
                    Hypothesis.status != HypothesisStatus.INACTIVE,
                    Hypothesis.merged_into_id.is_(None),
                    eligible,
                )
                .order_by(Hypothesis.id)
                .limit(100)
            )
        ).all()
        stale = state.status == "stale" or (
            state.fresh_until is not None and state.fresh_until <= datetime.now(UTC)
        )
        status = "stale" if state.status == "ready" and stale else state.status
        complete = bool(state.progress.get("complete"))
        if rows:
            message = (
                "Available intelligence is retained; research is in progress."
                if status == "researching"
                else "Available intelligence is retained."
            )
        elif status == "researching" and complete and state.initial_completed_at is not None:
            message = "Nothing was found in initial research; enrichment is in progress."
        elif status == "ready" and complete:
            message = "Nothing was found."
        elif status == "researching":
            message = "Research is in progress; no public findings are available yet."
        else:
            message = "Research is incomplete; no public findings are available."
        return LocationIntelligence(
            location_id=location_id,
            status=status,
            initial=state.initial_completed_at is not None,
            enriched=state.enriched_at is not None,
            stale=stale,
            in_progress=status == "researching",
            complete=complete,
            active_job_id=state.active_job_id,
            queue_pending=queue_pending,
            message=message,
            hypotheses=[
                {
                    "id": str(row.id),
                    "primary_location_id": str(row.primary_location_id),
                    "title": row.title,
                    "confidence_score": row.confidence_score,
                    "recency_score": row.recency_score,
                    "prevalence_score": row.prevalence_score,
                    "status": row.status.value,
                }
                for row in rows
            ],
            progress=state.progress,
        )


async def _owned(session, identity, token):
    job = await session.scalar(
        select(ResearchJob)
        .where(ResearchJob.id == identity)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    state = await session.scalar(
        select(LocationResearchState)
        .where(LocationResearchState.active_job_id == identity)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        job is None
        or state is None
        or job.run_token != token
        or state.run_token != token
        or job.status != JobStatus.RUNNING
        or job.lease_expires_at <= datetime.now(UTC)
    ):
        raise ResearchLeaseLost()
    return job, state


async def _take(ctx, identity, token):
    async with ctx["sessions"]() as session, session.begin():
        job = await session.scalar(
            select(ResearchJob).where(ResearchJob.id == identity).with_for_update()
        )
        if job is None or job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED}:
            return None
        if job.kind != JobKind.LOCATION_RESEARCH:
            raise ValueError("not_a_location_job")
        state = await session.scalar(
            select(LocationResearchState)
            .where(LocationResearchState.active_job_id == identity)
            .with_for_update()
        )
        if state is None:
            job.status, job.run_token, job.lease_expires_at = JobStatus.FAILED, None, None
            job.error_code = "location_run_superseded"
            return None
        now = datetime.now(UTC)
        if job.status == JobStatus.RUNNING and job.lease_expires_at > now:
            return None
        if job.attempts >= 3:
            job.status, job.run_token, job.lease_expires_at = JobStatus.FAILED, None, None
            state.status, state.run_token, state.lease_expires_at = "failed", None, None
            state.error_code = job.error_code = "attempts_exhausted"
            state.next_retry_at = now + timedelta(
                seconds=ctx.get("settings", Settings()).research_retry_seconds
            )
            return None
        job.attempts += 1
        job.status, job.run_token = JobStatus.RUNNING, token
        job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        state.run_token, state.lease_expires_at = token, job.lease_expires_at
        state.progress = {**state.progress, "phase": job.payload["phase"], "complete": False}
        return {**job.payload, "continuation": state.progress.get("continuation")}


async def _finish(ctx, identity, token, summary):
    settings = ctx.get("settings") or Settings()
    async with ctx["sessions"]() as session, session.begin():
        job, state = await _owned(session, identity, token)
        now = datetime.now(UTC)
        complete = bool(summary.get("complete"))
        job.result = summary
        job.status = JobStatus.FAILED if summary["status"] == "failed" else JobStatus.SUCCEEDED
        job.error_code = "research_incomplete" if not complete else None
        job.run_token = job.lease_expires_at = None
        state.progress = {**state.progress, **summary, "phase": job.payload["phase"]}
        state.error_code = job.error_code
        if complete:
            state.last_success_at = now
            state.fresh_until = now + timedelta(seconds=settings.research_fresh_seconds)
        saved = state.progress.get("continuation")
        cursor = Cursor.model_validate(saved) if saved else None
        more = bool(
            cursor
            and cursor.jobs < 3
            and cursor.seconds_reserved < cursor.seconds_cap
            and summary.get("continuation_pending")
            and summary.get("made_progress")
            and summary.get("continuation_possible")
            and "scheduler_limits" not in job.payload
        )
        if job.payload["phase"] == "initial":
            state.initial_completed_at = now
            if cursor:
                cursor.jobs += 1
                state.progress = {**state.progress, "continuation": cursor.model_dump(mode="json")}
            await _new_job(session, state, "enrichment")
        elif more:
            cursor.jobs += 1
            state.progress = {**state.progress, "continuation": cursor.model_dump(mode="json")}
            await _new_job(session, state, "continuation")
        else:
            if complete and state.initial_completed_at is None:
                state.initial_completed_at = now
            state.status = (
                "ready" if complete else "stale" if state.initial_completed_at else "failed"
            )
            if summary["status"] == "failed":
                state.status = "failed"
            state.enriched_at = now if complete else state.enriched_at
            state.run_token = state.lease_expires_at = None
            state.next_retry_at = (
                None if complete else now + timedelta(seconds=settings.research_retry_seconds)
            )


async def _retry(ctx, identity, token, error):
    settings = ctx.get("settings") or Settings()
    async with ctx["sessions"]() as session, session.begin():
        job, state = await _owned(session, identity, token)
        now = datetime.now(UTC)
        job.status = JobStatus.RETRY if job.attempts < 3 else JobStatus.FAILED
        job.run_token = job.lease_expires_at = None
        job.error_code = state.error_code = error
        state.lease_expires_at = now  # Expired but fenced until a retry obtains a new token.
        state.progress = {**state.progress, "complete": False, "error_code": error}
        if job.status == JobStatus.FAILED:
            state.status, state.run_token, state.lease_expires_at = "failed", None, None
            state.next_retry_at = now + timedelta(seconds=settings.research_retry_seconds)


async def run_location_job(ctx, job_id: str):
    identity, token = UUID(job_id), uuid4()
    from app.db.scheduler import SchedulerBudgetDeferred, ensure_execution_budget

    if not await ensure_execution_budget(ctx, identity):
        return
    payload = await _take(ctx, identity, token)
    if payload is None:
        return
    if "scheduler_limits" in payload:
        from app.db.scheduler import guarded_context

        ctx = guarded_context(ctx, identity, token=token)
    settings = ctx.get("settings") or Settings()
    limits = (
        ResearchLimits.model_validate(payload["scheduler_limits"])
        if "scheduler_limits" in payload
        else limits_for(settings, payload["phase"])
    )

    progress = None

    async def persist(session=None):
        if session is None:
            async with ctx["sessions"]() as owned_session:
                await persist(owned_session)
            return
        job, state = await _owned(session, identity, token)
        if job.lease_expires_at <= datetime.now(UTC):
            raise ResearchLeaseLost()
        state.progress = {
            **state.progress,
            "checkpoints": state.progress.get("checkpoints", 0) + 1,
            "continuation": progress.cursor.model_dump(mode="json"),
        }
        await session.commit()

    async def checkpoint(session):
        await progress.save(session)

    async def on_claim(session, claim_id, budget):
        return await apply_and_score_claim(
            session, claim_id, embedder=ctx["embedder"], client=budget
        )

    async def on_hypothesis(session, hypothesis_id):
        return await embed_hypothesis(session, hypothesis_id, embedder=ctx["embedder"])

    diagnostics = {}
    try:
        if payload.get("continuation"):
            cursor = Cursor.model_validate(payload["continuation"])
        else:
            initial = limits_for(settings, "initial")
            extra = limits_for(settings, "enrichment")
            phases = [initial, extra] if payload["phase"] == "initial" else [limits]
            cursor = Cursor(
                caps={
                    name: sum(getattr(p, name) for p in phases)
                    for name in ("queries", "fetches", "documents", "llm_calls")
                },
                seconds_cap=3 * sum(p.timeout for p in phases),
            )
        progress = Progress(cursor, persist)
        granted = await progress.reserve_time(limits.timeout)
        if granted <= 0:
            summary = {"status": "partial", "complete": False, "error_code": "run_time_limit"}
            await _finish(ctx, identity, token, summary)
            return
        limits = limits.model_copy(update={"timeout": granted})
        async with asyncio.timeout(limits.timeout):
            async with ctx["sessions"]() as session:
                path = await get_location_path(session, UUID(payload["location_id"]))
                # Name is only a search label; target identity remains the caller's resolved UUID.
                result = await research_location(
                    session,
                    search_label(path),
                    target_location_id=path[-1].id,
                    provider=ctx["provider"],
                    fetcher=ctx["fetcher"],
                    llm=ctx["llm"],
                    storage=ctx["storage"],
                    bucket=ctx["bucket"],
                    limits=limits,
                    checkpoint=checkpoint,
                    on_claim=on_claim,
                    on_hypothesis=on_hypothesis,
                    diagnostics=diagnostics,
                    progress=progress,
                )
                await checkpoint(session)
                summary = result.model_dump(mode="json")
    except SchedulerBudgetDeferred:
        try:
            await _retry(ctx, identity, token, "daily_scheduler_limit")
        except ResearchLeaseLost:
            pass
        return
    except ResearchLeaseLost:
        return
    except TimeoutError:
        summary = {
            "status": "partial",
            "complete": False,
            "error_code": "deadline",
            "message": "Time limit reached; completed stages are retained.",
            "usage_complete": False,
            "diagnostics": diagnostics,
        }
    except asyncio.CancelledError:
        try:
            await asyncio.shield(_retry(ctx, identity, token, "cancelled"))
        except ResearchLeaseLost:
            pass
        raise
    except Exception as exc:
        try:
            await _retry(ctx, identity, token, type(exc).__name__)
        except ResearchLeaseLost:
            pass
        return
    try:
        if progress:
            summary["continuation_pending"] = progress.has_work()
            summary["continuation_possible"] = progress.can_advance()
            if progress.has_work() and not progress.can_advance():
                summary["stop_reason"] = "resource_limit"
            summary["made_progress"] = progress.cursor.used != progress.start_used and any(
                i.get("document_id") for i in progress.cursor.candidates.values()
            )
        await _finish(ctx, identity, token, summary)
    except ResearchLeaseLost:
        return
