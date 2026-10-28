"""Refresh current eligible evidence and persist explainable confidence snapshots."""

from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select

from app.confidence import POLICY, VERSION, score_confidence
from app.db.duplicates import _hash
from app.db.evidence_weight import calculate_evidence_weights
from app.models import (
    Claim,
    Evidence,
    EvidenceWeightRun,
    Hypothesis,
    HypothesisScoreSnapshot,
    IndependenceRun,
)


class ConfidenceResult(BaseModel):
    version: str = VERSION
    snapshot_id: UUID
    weight_run_id: UUID
    score: float
    label: str
    complete: bool
    review_reasons: list[str]
    created: bool
    explanation: str


async def calculate_confidence(
    session, hypothesis_id: UUID, *, limit: int = 100
) -> ConfidenceResult:
    async with session.begin_nested():
        weights = await calculate_evidence_weights(session, hypothesis_id, limit=limit)
        run = await session.get(EvidenceWeightRun, weights.run_id)
        independent = await session.get(IndependenceRun, run.independence_run_id)
        origins = {item["evidence_id"]: item for item in independent.snapshot["evidence"]}
        hypothesis = await session.get(Hypothesis, hypothesis_id)
        rows = (
            await session.execute(
                select(Evidence, Claim.location_id)
                .join(Claim, Evidence.claim_id == Claim.id)
                .where(Evidence.hypothesis_id == hypothesis_id, Evidence.archived_at.is_(None))
                .order_by(Evidence.id)
                .execution_options(populate_existing=True)
            )
        ).all()
        if {str(row.Evidence.id) for row in rows} != set(run.scores):
            raise ValueError("confidence_cohort_changed")
        items = []
        for row in rows:
            evidence = row.Evidence
            key = str(evidence.id)
            if (
                evidence.weight_run_id != run.id
                or evidence.evidence_weight != run.scores[key]["weight"]
            ):
                raise ValueError("current_evidence_weight_required")
            items.append(
                {
                    "evidence_id": key,
                    "claim_id": str(evidence.claim_id),
                    "weight": evidence.evidence_weight,
                    "stance": evidence.stance.value,
                    "semantic_match_score": evidence.semantic_match_score,
                    "location_agreement": "unknown"
                    if row.location_id is None
                    else "exact"
                    if row.location_id == hypothesis.primary_location_id
                    else "different",
                    "source_type": run.scores[key]["components"]["effective_source_type"],
                    "platform": origins[key]["platform"],
                }
            )
        inputs = {
            "policy": POLICY,
            "weight_run_id": str(run.id),
            "evidence": items,
            "hypothesis": independent.snapshot["hypothesis"],
            "complete": weights.complete,
            "review_reasons": weights.review_reasons,
        }
        fingerprint = _hash(inputs)
        snapshot = await session.scalar(
            select(HypothesisScoreSnapshot).where(
                HypothesisScoreSnapshot.hypothesis_id == hypothesis_id,
                HypothesisScoreSnapshot.scoring_version == VERSION,
                HypothesisScoreSnapshot.input_hash == fingerprint,
            )
        )
        created = snapshot is None
        if created:
            scored = score_confidence(
                items, complete=weights.complete, review_reasons=weights.review_reasons
            )
            snapshot = HypothesisScoreSnapshot(
                hypothesis_id=hypothesis_id,
                confidence_score=scored["score"],
                prevalence_score=None,
                recency_score=None,
                scoring_version=VERSION,
                input_hash=fingerprint,
                weight_run_id=run.id,
                explanation=scored["explanation"],
                components={**scored["components"], "inputs": inputs},
            )
            session.add(snapshot)
        hypothesis.confidence_score = snapshot.confidence_score
        await session.flush()
        return ConfidenceResult(
            snapshot_id=snapshot.id,
            weight_run_id=run.id,
            score=snapshot.confidence_score,
            label=snapshot.components["label"],
            complete=snapshot.components["complete"],
            review_reasons=snapshot.components["review_reasons"],
            created=created,
            explanation=snapshot.explanation,
        )
