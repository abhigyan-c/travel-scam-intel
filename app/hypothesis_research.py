"""Targeted discovery through the same bounded acquisition and claim pipeline."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from app.db.claim_pipeline import apply_and_score_claim, embed_hypothesis
from app.db.resolution import get_location_path
from app.models import Hypothesis, HypothesisResearchState, ResearchJob
from app.on_demand import search_label
from app.research import ResearchLeaseLost, ResearchLimits, research_location
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

    async def checkpoint(session):
        job = await session.scalar(
            select(ResearchJob)
            .where(ResearchJob.id == identity, ResearchJob.run_token == token)
            .with_for_update()
        )
        visible = await session.scalar(
            select(Hypothesis.id).where(Hypothesis.id == hypothesis_id, current_hypothesis())
        )
        if job is None or job.lease_expires_at <= datetime.now(UTC) or visible is None:
            raise ResearchLeaseLost()
        await session.commit()

    async def on_claim(session, claim_id, budget):
        return await apply_and_score_claim(
            session, claim_id, embedder=ctx["embedder"], client=budget
        )

    async def on_hypothesis(session, hypothesis_id):
        return await embed_hypothesis(session, hypothesis_id, embedder=ctx["embedder"])

    result = await research_location(
        session,
        search_label(path),
        target_location_id=target.primary_location_id,
        topic=topic,
        provider=ctx["provider"],
        fetcher=ctx["fetcher"],
        llm=ctx["llm"],
        storage=ctx["storage"],
        bucket=ctx["bucket"],
        limits=ResearchLimits.model_validate(payload["scheduler_limits"]),
        checkpoint=checkpoint,
        on_claim=on_claim,
        on_hypothesis=on_hypothesis,
    )
    await checkpoint(session)
    return result.model_dump(mode="json")


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
