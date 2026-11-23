"""Targeted discovery through the same bounded acquisition and claim pipeline."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from app.db.claim_pipeline import apply_and_score_claim, embed_hypothesis
from app.db.resolution import get_location_path
from app.models import Hypothesis, HypothesisResearchState, ResearchJob
from app.on_demand import search_label
from app.research import ResearchLeaseLost, ResearchLimits, research_location
from app.research_progress import Cursor, Progress
from app.services.intelligence import IntelligenceNotFound, _Query
from app.services.public_evidence import current_hypothesis, public_text


async def perform_hypothesis_research(ctx, payload, session, identity, token):
    hypothesis_id = UUID(payload["hypothesis_id"])
    state = await session.get(HypothesisResearchState, hypothesis_id)
    if state is None or state.active_job_id != identity:
        return {"status": "skipped", "complete": False, "reason": "superseded"}
    query = _Query(session, datetime.now(UTC))
    try:
        target = await query.visible(hypothesis_id)
    except IntelligenceNotFound:
        return {"status": "skipped", "complete": False, "reason": "not_public"}
    summary = await session.scalar(
        select(query.summary()).select_from(Hypothesis).where(Hypothesis.id == hypothesis_id)
    )
    topic = public_text(summary, 120)
    if topic == "[Content withheld]":
        return {"status": "skipped", "complete": False, "reason": "topic_withheld"}
    path = await get_location_path(session, target.primary_location_id)

    limits = ResearchLimits.model_validate(payload["scheduler_limits"])
    job = await session.get(ResearchJob, identity)
    saved = job.result.get("continuation")
    cursor = (
        Cursor.model_validate(saved)
        if saved
        else Cursor(
            caps={
                name: getattr(limits, name)
                for name in ("queries", "fetches", "documents", "llm_calls")
            },
            seconds_cap=3 * limits.timeout,
        )
    )

    async def persist(session=None):
        if session is None:
            async with ctx["sessions"]() as owned_session:
                await persist(owned_session)
            return
        job = await session.scalar(
            select(ResearchJob)
            .where(ResearchJob.id == identity, ResearchJob.run_token == token)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        visible = await session.scalar(
            select(Hypothesis.id).where(Hypothesis.id == hypothesis_id, current_hypothesis())
        )
        if job is None or job.lease_expires_at <= datetime.now(UTC) or visible is None:
            raise ResearchLeaseLost()
        job.result = {**job.result, "continuation": cursor.model_dump(mode="json")}
        await session.commit()

    progress = Progress(cursor, persist)

    async def checkpoint(session):
        await progress.save(session)

    # Release the read transaction before reserving budget in an independent session.
    target_location_id = target.primary_location_id
    await session.commit()
    granted = await progress.reserve_time(limits.timeout)
    if granted <= 0:
        return {
            "status": "partial",
            "complete": False,
            "error_code": "run_time_limit",
            "continuation": cursor.model_dump(mode="json"),
        }
    limits = limits.model_copy(update={"timeout": granted})

    async def on_claim(session, claim_id, budget):
        return await apply_and_score_claim(
            session, claim_id, embedder=ctx["embedder"], client=budget
        )

    async def on_hypothesis(session, hypothesis_id):
        return await embed_hypothesis(session, hypothesis_id, embedder=ctx["embedder"])

    result = await research_location(
        session,
        search_label(path),
        target_location_id=target_location_id,
        topic=topic,
        provider=ctx["provider"],
        fetcher=ctx["fetcher"],
        llm=ctx["llm"],
        storage=ctx["storage"],
        bucket=ctx["bucket"],
        limits=limits,
        checkpoint=checkpoint,
        on_claim=on_claim,
        on_hypothesis=on_hypothesis,
        progress=progress,
    )
    await checkpoint(session)
    return {**result.model_dump(mode="json"), "continuation": cursor.model_dump(mode="json")}


async def finish_hypothesis_state(session, payload, identity, settings, *, complete):
    state = await session.get(
        HypothesisResearchState, UUID(payload["hypothesis_id"]), populate_existing=True
    )
    if state is not None and state.active_job_id == identity:
        now = datetime.now(UTC)
        state.last_attempt_at = now
        state.last_success_at = now if complete else state.last_success_at
        state.next_retry_at = (
            None if complete else now + timedelta(seconds=settings.research_retry_seconds)
        )
