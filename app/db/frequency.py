"""Refresh corroboration and persist separate, reproducible temporal measurements."""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select

from app.db.confidence import calculate_confidence
from app.db.duplicates import _hash
from app.frequency import VERSION, calculate_frequency, utc
from app.models import (
    Claim,
    Document,
    Evidence,
    EvidenceWeightRun,
    Hypothesis,
    HypothesisScoreSnapshot,
    UserReport,
)


class FrequencyResult(BaseModel):
    version: str = VERSION
    snapshot_id: UUID
    confidence_snapshot_id: UUID
    as_of: datetime
    recency_score: float | None
    prevalence_score: float | None
    trend: str
    complete: bool
    created: bool
    cache_updated: bool
    components: dict


async def calculate_recency_and_frequency(
    session, hypothesis_id: UUID, *, as_of: datetime | None = None, limit: int = 100
) -> FrequencyResult:
    # Explicit reference dates produce reproducible measurements, not a historical DB replay.
    cache_updated = as_of is None
    reference = utc(as_of) if as_of is not None else datetime.now(UTC).replace(microsecond=0)
    async with session.begin_nested():
        confidence = await calculate_confidence(session, hypothesis_id, limit=limit)
        weight_run = await session.get(EvidenceWeightRun, confidence.weight_run_id)
        weight_inputs = {row["evidence_id"]: row for row in weight_run.snapshot["evidence"]}
        hypothesis = await session.get(Hypothesis, hypothesis_id)
        rows = (
            await session.execute(
                select(Evidence, Claim, Document)
                .join(Claim, Evidence.claim_id == Claim.id)
                .join(Document, Claim.document_id == Document.id)
                .where(Evidence.hypothesis_id == hypothesis_id, Evidence.archived_at.is_(None))
                .order_by(Evidence.id)
                .execution_options(populate_existing=True)
            )
        ).all()
        if {str(row.Evidence.id) for row in rows} != set(weight_run.scores):
            raise ValueError("frequency_cohort_changed")
        # The preceding weighting stage holds these report locks through this transaction.
        reports = {
            row.document_id: row
            for row in (
                await session.scalars(
                    select(UserReport)
                    .where(UserReport.document_id.in_([row.Document.id for row in rows]))
                    .order_by(UserReport.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        }
        items = []
        for row in rows:
            evidence, claim, document = row.Evidence, row.Claim, row.Document
            key = str(evidence.id)
            if (
                evidence.weight_run_id != weight_run.id
                or evidence.independence_run_id != weight_run.independence_run_id
            ):
                raise ValueError("current_frequency_provenance_required")
            origin = weight_inputs[key]
            report = reports.get(document.id)
            report_occurrence = report.occurred_at if report else None
            occurrence = report_occurrence if report_occurrence is not None else claim.occurred_at
            reported = report.created_at if report else None
            if report is None and origin["published_at"]:
                reported = datetime.fromisoformat(origin["published_at"])
            items.append(
                {
                    "evidence_id": key,
                    "claim_id": str(claim.id),
                    "document_id": str(document.id),
                    "report_key": f"user_report:{report.id}"
                    if report
                    else f"source:{origin['source_id']}",
                    "eligible": weight_run.scores[key]["components"]["public_eligible"],
                    "stance": evidence.stance.value,
                    "location_agreement": "exact"
                    if claim.location_id == hypothesis.primary_location_id
                    else "unknown"
                    if claim.location_id is None
                    else "different",
                    "independence": origin["independence"],
                    "occurred_at": utc(occurrence).isoformat() if occurrence is not None else None,
                    "occurrence_basis": "user_report"
                    if report_occurrence is not None
                    else "claim"
                    if occurrence is not None
                    else "unknown",
                    "reported_at": utc(reported).isoformat() if reported is not None else None,
                    "reporting_basis": "user_report_submission"
                    if report
                    else origin["publication_basis"],
                }
            )
        components = calculate_frequency(items, as_of=reference)
        components["confidence_snapshot_id"] = str(confidence.snapshot_id)
        components["weight_run_id"] = str(weight_run.id)
        components["complete"] = confidence.complete and not components["date_review_reasons"]
        components["review_reasons"] = sorted(
            set(confidence.review_reasons + components["date_review_reasons"])
        )
        # Include all excluded primitive inputs too: date/eligibility edits must change history.
        components["inputs"] = items
        fingerprint = _hash(components)
        snapshot = await session.scalar(
            select(HypothesisScoreSnapshot).where(
                HypothesisScoreSnapshot.hypothesis_id == hypothesis_id,
                HypothesisScoreSnapshot.scoring_version == VERSION,
                HypothesisScoreSnapshot.input_hash == fingerprint,
            )
        )
        created = snapshot is None
        if created:
            snapshot = HypothesisScoreSnapshot(
                hypothesis_id=hypothesis_id,
                confidence_score=confidence.score,
                recency_score=components["recency_score"],
                prevalence_score=components["prevalence_score"],
                scoring_version=VERSION,
                input_hash=fingerprint,
                weight_run_id=weight_run.id,
                components=components,
                explanation=(
                    f"Observed reports at {reference.isoformat()}; occurrence dates determine "
                    "recency and 30/90/365-day frequency. Publication/submission dates are "
                    "counted separately. Missing dates remain unknown. "
                    "Prevalence is a reporting-frequency heuristic, not population incidence."
                ),
            )
            session.add(snapshot)
        if cache_updated:
            hypothesis.recency_score = snapshot.recency_score
            hypothesis.prevalence_score = snapshot.prevalence_score
        await session.flush()
        return FrequencyResult(
            snapshot_id=snapshot.id,
            confidence_snapshot_id=confidence.snapshot_id,
            as_of=reference,
            recency_score=snapshot.recency_score,
            prevalence_score=snapshot.prevalence_score,
            trend=snapshot.components["trend"]["classification"],
            complete=snapshot.components["complete"],
            created=created,
            cache_updated=cache_updated,
            components=snapshot.components,
        )
