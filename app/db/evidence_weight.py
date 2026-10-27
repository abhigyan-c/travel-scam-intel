"""Refresh independence, then grade source usefulness and public evidence contribution."""

import asyncio
import math
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.duplicates import _hash
from app.db.independence import calculate_independence
from app.evidence_weight import POLICY, VERSION, grade_evidence
from app.models import (
    Claim,
    Document,
    Evidence,
    EvidenceWeightRun,
    Hypothesis,
    IndependenceRun,
    Source,
    UserReport,
)


class EvidenceWeightResult(BaseModel):
    version: str = VERSION
    run_id: UUID
    independence_run_id: UUID
    evidence: int
    eligible: int
    excluded: int
    changed: int
    total_weight: float
    complete: bool
    review_reasons: list[str]


async def calculate_evidence_weights(
    session, hypothesis_id: UUID, *, limit: int = 100
) -> EvidenceWeightResult:
    async with session.begin_nested():
        independence = await calculate_independence(session, hypothesis_id, limit=limit)
        independent_run = await session.get(IndependenceRun, independence.run_id)
        origin = {item["evidence_id"]: item for item in independent_run.snapshot["evidence"]}
        rows = (
            await session.execute(
                select(
                    Evidence,
                    Claim,
                    Document.id.label("document_id"),
                    Source.source_type,
                    Source.source_metadata["official_advisory_verified"].label("official_verified"),
                )
                .join(Claim, Evidence.claim_id == Claim.id)
                .join(Document, Claim.document_id == Document.id)
                .join(Source, Document.source_id == Source.id)
                .where(
                    Evidence.hypothesis_id == hypothesis_id,
                    Evidence.archived_at.is_(None),
                )
                .order_by(Evidence.id)
                .execution_options(populate_existing=True)
            )
        ).all()
        if {str(row.Evidence.id) for row in rows} != set(origin):
            raise ValueError("weight_cohort_changed")
        reports = {
            row.document_id: row
            for row in (
                await session.scalars(
                    select(UserReport)
                    .where(
                        UserReport.document_id.in_([row.document_id for row in rows]),
                    )
                    .order_by(UserReport.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        }
        inputs = []
        for row in rows:
            evidence, claim = row.Evidence, row.Claim
            earlier = origin[str(evidence.id)]
            if (
                evidence.independence_run_id != independence.run_id
                or evidence.independence_score is None
                or not math.isfinite(evidence.independence_score)
                or not 0 <= evidence.independence_score <= 1
            ):
                raise ValueError("current_independence_required")
            report = reports.get(row.document_id)
            first_hand = claim.structured_data.get("first_hand")
            provenance = (
                report.provenance_class.value
                if report
                else (
                    "FIRST_HAND"
                    if first_hand is True
                    else "SECOND_HAND"
                    if first_hand is False
                    else "UNKNOWN"
                )
            )
            sensitive = claim.structured_data.get("sensitive")
            publication = claim.structured_data.get("publication_status")
            if not isinstance(publication, str) or len(publication) > 50:
                publication = "unknown"
            official = row.official_verified if type(row.official_verified) is bool else None
            inputs.append(
                {
                    "evidence_id": str(evidence.id),
                    "claim_id": str(claim.id),
                    "document_id": str(row.document_id),
                    "source_id": earlier["source_id"],
                    "source_type": row.source_type.value,
                    "official_verified": official,
                    "invalid_official_metadata": row.official_verified is not None
                    and official is None,
                    "report_id": str(report.id) if report else None,
                    "report_status": report.moderation_status if report else None,
                    "provenance": provenance,
                    "invalid_provenance": report is None
                    and first_hand is not None
                    and type(first_hand) is not bool,
                    "known_author": bool(earlier["author"]),
                    "published_at": earlier["published_at"],
                    "publication_basis": earlier["publication_basis"],
                    "publication_status": publication,
                    "sensitive": sensitive is True,
                    "invalid_sensitivity": sensitive is not None and type(sensitive) is not bool,
                    "named_actors": bool(claim.structured_data.get("entities")),
                    "stance": evidence.stance.value,
                    "independence": evidence.independence_score,
                }
            )
        snapshot = {
            "policy": POLICY,
            "independence_run_id": str(independence.run_id),
            "hypothesis": independent_run.snapshot["hypothesis"],
            "evidence": inputs,
            "independence_complete": independence.complete,
            "review_reasons": independence.review_reasons,
        }
        fingerprint = _hash(snapshot)
        run = await session.scalar(
            select(EvidenceWeightRun).where(
                EvidenceWeightRun.hypothesis_id == hypothesis_id,
                EvidenceWeightRun.version == VERSION,
                EvidenceWeightRun.input_hash == fingerprint,
            )
        )
        created = run is None
        if created:
            scores = await asyncio.to_thread(
                lambda: {item["evidence_id"]: grade_evidence(item) for item in inputs}
            )
            identity = await session.scalar(
                insert(EvidenceWeightRun)
                .values(
                    hypothesis_id=hypothesis_id,
                    independence_run_id=independence.run_id,
                    version=VERSION,
                    input_hash=fingerprint,
                    snapshot=snapshot,
                    scores=scores,
                )
                .returning(EvidenceWeightRun.id)
            )
            run = await session.get(EvidenceWeightRun, identity)
        changed = 0
        for row in rows:
            evidence = row.Evidence
            score = run.scores[str(evidence.id)]
            if (
                evidence.weight_run_id != run.id
                or evidence.source_quality_score != score["source_quality"]
                or evidence.evidence_weight != score["weight"]
                or evidence.weight_components != score["components"]
            ):
                evidence.source_quality_score = score["source_quality"]
                evidence.evidence_weight = score["weight"]
                evidence.weight_run_id, evidence.weight_components = run.id, score["components"]
                changed += 1
        if changed or created:
            hypothesis = await session.get(Hypothesis, hypothesis_id)
            hypothesis.confidence_score = hypothesis.prevalence_score = hypothesis.recency_score = (
                None
            )
        await session.flush()
        eligible = sum(item["components"]["public_eligible"] for item in run.scores.values())
        return EvidenceWeightResult(
            run_id=run.id,
            independence_run_id=independence.run_id,
            evidence=len(rows),
            eligible=eligible,
            excluded=len(rows) - eligible,
            changed=changed,
            total_weight=round(sum(item["weight"] for item in run.scores.values()), 6),
            complete=independence.complete,
            review_reasons=independence.review_reasons,
        )
