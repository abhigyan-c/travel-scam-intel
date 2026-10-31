"""Shared, review-preserving claim application for research and normalized reports."""

from sqlalchemy import select

from app.db.embeddings import backfill_embeddings
from app.db.emergence import detect_emergence
from app.db.frequency import calculate_recency_and_frequency
from app.db.hypothesis_lifecycle import apply_resolution
from app.db.hypothesis_resolution import _scope, resolve_hypothesis
from app.db.resolution import get_location_path
from app.models import Claim, Hypothesis


async def apply_and_score_claim(session, claim_id, *, embedder, client):
    claim = await session.get(Claim, claim_id)
    if claim.location_id is not None:
        path = await get_location_path(session, claim.location_id)
        existing = await session.scalar(
            select(Hypothesis.id).where(_scope(claim.location_id, path)).limit(1)
        )
        if existing is not None:
            try:
                await embedder.preload()
            except (ValueError, RuntimeError, OSError):
                pass  # The resolver persists missing-model/coverage errors without guessing.
    resolved = await resolve_hypothesis(session, claim_id, embedder=embedder, client=client)
    if resolved.error or resolved.needs_review:
        return {
            "pending_resolution_ids": [str(resolved.id)],
            "error": resolved.error or "review_required",
        }
    event = await apply_resolution(session, resolved.id, embedder=embedder)
    await calculate_recency_and_frequency(session, event.target_id)
    await detect_emergence(session, event.target_id)
    return {"hypothesis_ids": [str(event.target_id)]}


async def embed_hypothesis(session, hypothesis_id, *, embedder):
    try:
        await embedder.preload()
        result = await backfill_embeddings(session, Hypothesis, embedder, ids=[hypothesis_id])
        error = "embedding_unavailable" if result["errors"] else None
    except (ValueError, RuntimeError, OSError):
        error = "embedding_unavailable"
    return {"error": error}
