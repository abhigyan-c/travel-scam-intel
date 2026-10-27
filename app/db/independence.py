"""Refresh duplicate provenance, then persist deterministic cohort contribution scores."""

import asyncio
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.duplicates import _hash, _url, detect_duplicates
from app.independence import POLICY, VERSION, score_evidence
from app.models import (
    Claim,
    Document,
    DuplicateAssessment,
    DuplicateDetectionRun,
    Evidence,
    Hypothesis,
    IndependenceRun,
    Source,
)


def _references(value):
    if value is None:
        return [], 0
    if not isinstance(value, list):
        return [], 1
    if len(value) > 20:
        raise ValueError("independence_reference_limit_exceeded")
    valid, invalid = set(), 0
    for entry in value:
        url = _url(entry) if isinstance(entry, str) and len(entry) <= 2048 else None
        if url is None:
            invalid += 1
        else:
            valid.add(url)
    return sorted(valid), invalid


def _iso(value):
    return value.isoformat() if value else None


class IndependenceResult(BaseModel):
    version: str = VERSION
    run_id: UUID
    duplicate_run_id: UUID
    evidence: int
    changed: int
    total_credit: float
    complete: bool
    review_reasons: list[str]


async def calculate_independence(
    session, hypothesis_id: UUID, *, limit: int = 100
) -> IndependenceResult:
    async with session.begin_nested():
        detected = await detect_duplicates(session, hypothesis_id, limit=limit)
        duplicate_run = await session.get(DuplicateDetectionRun, detected.run_id)
        by_evidence = {item["evidence_id"]: item for item in duplicate_run.snapshot["claims"]}
        rows = (
            await session.execute(
                select(
                    Evidence,
                    Claim.created_at.label("claim_created_at"),
                    Document.url.label("document_url"),
                    Document.published_at.label("document_published_at"),
                    Source.published_at.label("source_published_at"),
                    Source.source_metadata["reference_urls"].label("reference_urls"),
                    Source.source_metadata["cross_post_of"].label("raw_cross_post"),
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
        if {str(row.Evidence.id) for row in rows} != set(by_evidence):
            raise ValueError("independence_cohort_changed")
        inputs = []
        reasons = list(detected.review_reasons)
        for row in rows:
            evidence = row.Evidence
            prior = by_evidence[str(evidence.id)]
            refs, invalid = _references(row.reference_urls)
            urls = sorted({url for url in (prior["url"], _url(row.document_url)) if url})
            cross_post = prior["cross_post_of"]
            if cross_post in urls:
                cross_post = None
                invalid += 1
            if row.raw_cross_post is not None and prior["cross_post_of"] is None:
                invalid += 1
            if invalid:
                reasons.append("invalid_reference_urls")
            published = row.document_published_at or row.source_published_at
            inputs.append(
                {
                    "evidence_id": str(evidence.id),
                    "claim_id": prior["claim_id"],
                    "source_id": prior["source_id"],
                    "group_id": str(evidence.duplicate_group_id)
                    if evidence.duplicate_group_id
                    else None,
                    "author": prior["author"],
                    "platform": prior["platform"],
                    "thread": prior["thread"],
                    "urls": urls,
                    "reference_urls": refs,
                    "invalid_reference_count": invalid,
                    "cross_post_of": cross_post,
                    "published_at": _iso(published),
                    "publication_basis": "document"
                    if row.document_published_at
                    else "source"
                    if row.source_published_at
                    else "unknown",
                    "document_published_at": _iso(row.document_published_at),
                    "source_published_at": _iso(row.source_published_at),
                    "created_at": _iso(row.claim_created_at),
                }
            )
        assessments = (
            await session.scalars(
                select(DuplicateAssessment).where(
                    DuplicateAssessment.run_id == detected.run_id,
                )
            )
        ).all()
        if len(assessments) != len(inputs) * (len(inputs) - 1) // 2:
            raise ValueError("incomplete_duplicate_assessments")
        pairs = {
            (str(row.left_claim_id), str(row.right_claim_id)): {
                "classification": row.classification,
                "signals": row.signals,
            }
            for row in assessments
        }
        hypothesis = await session.get(Hypothesis, hypothesis_id)
        snapshot = {
            "policy": POLICY,
            "duplicate_run_id": str(detected.run_id),
            "duplicate_complete": detected.complete,
            "review_reasons": sorted(set(reasons)),
            "hypothesis": {
                "id": str(hypothesis_id),
                "location_id": str(hypothesis.primary_location_id),
                "title": hypothesis.title,
                "description": hypothesis.canonical_description,
                "category": hypothesis.category,
                "status": hypothesis.status.value,
            },
            "evidence": inputs,
        }
        fingerprint = _hash(snapshot)
        run = await session.scalar(
            select(IndependenceRun).where(
                IndependenceRun.hypothesis_id == hypothesis_id,
                IndependenceRun.version == VERSION,
                IndependenceRun.input_hash == fingerprint,
            )
        )
        created_run = run is None
        if created_run:
            scores = await asyncio.to_thread(score_evidence, inputs, pairs)
            identity = await session.scalar(
                insert(IndependenceRun)
                .values(
                    hypothesis_id=hypothesis_id,
                    duplicate_run_id=detected.run_id,
                    version=VERSION,
                    input_hash=fingerprint,
                    snapshot=snapshot,
                    scores=scores,
                )
                .returning(IndependenceRun.id)
            )
            run = await session.get(IndependenceRun, identity)
        changed = 0
        for row in rows:
            evidence = row.Evidence
            result = run.scores[str(evidence.id)]
            if (
                evidence.independence_run_id != run.id
                or evidence.independence_score != result["score"]
                or evidence.independence_components != result["components"]
            ):
                evidence.independence_run_id = run.id
                evidence.independence_score = result["score"]
                evidence.independence_components = result["components"]
                evidence.evidence_weight = None
                changed += 1
        if changed or created_run:
            hypothesis.confidence_score = hypothesis.prevalence_score = hypothesis.recency_score = (
                None
            )
        await session.flush()
        return IndependenceResult(
            run_id=run.id,
            duplicate_run_id=detected.run_id,
            evidence=len(rows),
            changed=changed,
            total_credit=round(sum(value["score"] for value in run.scores.values()), 6),
            complete=not reasons,
            review_reasons=sorted(set(reasons)),
        )
