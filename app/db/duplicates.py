"""Bounded, hypothesis-scoped duplicate assessment; callers own commits."""

import asyncio
import json
from itertools import combinations
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert

from app.db.evidence import invalidate_independence
from app.duplicates import POLICY, VERSION, compare, identity, normalize
from app.embeddings import MODEL, REVISION, text_hash, validate_vector
from app.integrations.fetch import canonicalize_url
from app.models import (
    Claim,
    Document,
    DuplicateAssessment,
    DuplicateDetectionRun,
    DuplicateGroup,
    Evidence,
    Hypothesis,
    Source,
)
from app.models.hypothesis import HypothesisStatus


def _hash(value):
    return text_hash(json.dumps(value, sort_keys=True))


def _url(value):
    try:
        return canonicalize_url(value) if value else None
    except ValueError:
        return None


def _vector(claim):
    if (
        claim.embedding is None
        or claim.embedding_model != MODEL
        or claim.embedding_revision != REVISION
        or claim.embedding_input_hash != text_hash(claim.claim_text)
    ):
        return None
    try:
        return validate_vector(claim.embedding)
    except (ValueError, TypeError):
        return None


def _assess(snapshots):
    pairs = {}
    for left, right in combinations(snapshots, 2):
        pairs[left["claim_id"], right["claim_id"]] = compare(left, right)
    # Complete-link components prevent an ambiguous bridge from joining incompatible reports.
    groups = []
    for item in snapshots:
        for component in groups:
            if all(
                pairs[tuple(sorted((item["claim_id"], other["claim_id"])))]["classification"]
                == "duplicate"
                for other in component
            ):
                component.append(item)
                break
        else:
            groups.append([item])
    return pairs, [component for component in groups if len(component) > 1]


class DetectionResult(BaseModel):
    version: str = VERSION
    run_id: UUID
    evidence: int
    assessments: int
    suspected: int
    groups: list[UUID]
    changed: int
    complete: bool
    review_reasons: list[str]


async def detect_duplicates(session, hypothesis_id: UUID, *, limit: int = 100) -> DetectionResult:
    if not 1 <= limit <= 200:
        raise ValueError("duplicate_limit_must_be_1_to_200")
    async with session.begin_nested():
        hypothesis = await session.scalar(
            select(Hypothesis)
            .where(Hypothesis.id == hypothesis_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if hypothesis is None:
            raise ValueError("hypothesis_not_found")
        if hypothesis.merged_into_id is not None or hypothesis.status == HypothesisStatus.INACTIVE:
            raise ValueError("hypothesis_not_current")
        clean_hash = func.encode(func.sha256(func.convert_to(Document.clean_text, "UTF8")), "hex")
        rows = (
            await session.execute(
                select(
                    Evidence,
                    Claim,
                    Document.id.label("document_id"),
                    Document.content_hash,
                    func.length(Document.clean_text).label("clean_length"),
                    case(
                        (Document.clean_text_hash == clean_hash, Document.clean_text_hash),
                        else_=None,
                    ).label("clean_hash"),
                    Source.id.label("source_id"),
                    Source.platform,
                    Source.canonical_url,
                    Source.author_identifier,
                    case(
                        (
                            func.jsonb_typeof(Source.source_metadata["thread_id"]) == "string",
                            Source.source_metadata["thread_id"].astext,
                        ),
                        else_=None,
                    ).label("thread_id"),
                    Source.source_metadata["cross_post_of"].astext.label("cross_post_of"),
                )
                .join(Claim, Evidence.claim_id == Claim.id)
                .join(Document, Claim.document_id == Document.id)
                .join(Source, Document.source_id == Source.id)
                .where(
                    Evidence.hypothesis_id == hypothesis_id,
                    Evidence.archived_at.is_(None),
                )
                .order_by(Claim.id)
                .limit(limit + 1)
                .with_for_update(of=(Evidence, Claim, Document, Source))
                .execution_options(populate_existing=True)
            )
        ).all()
        if len(rows) > limit:
            raise ValueError("duplicate_evidence_limit_exceeded")
        old_groups = {
            row.id: row
            for row in (
                await session.scalars(
                    select(DuplicateGroup).where(
                        DuplicateGroup.id.in_(
                            [
                                row.Evidence.duplicate_group_id
                                for row in rows
                                if row.Evidence.duplicate_group_id is not None
                            ]
                        ),
                    )
                )
            ).all()
        }
        snapshots = []
        for row in rows:
            evidence, claim = row.Evidence, row.Claim
            quote = claim.structured_data.get("evidence_quote") or ""
            if not isinstance(quote, str) or len(quote) > 4000 or len(claim.claim_text) > 4000:
                raise ValueError("duplicate_input_too_large_or_invalid")
            entities = claim.structured_data.get("entities") or []
            if (
                not isinstance(entities, list)
                or len(entities) > 10
                or any(not isinstance(value, str) or len(value) > 200 for value in entities)
            ):
                raise ValueError("invalid_duplicate_entities")
            extra_context = {}
            for field, key in (("location", "raw_location"), ("occurred_at", "occurrence_text")):
                value = claim.structured_data.get(field)
                if value is not None and (not isinstance(value, str) or len(value) > 200):
                    raise ValueError("invalid_duplicate_context")
                extra_context[key] = normalize(value) or None
            group = old_groups.get(evidence.duplicate_group_id)
            extraction = claim.structured_data.get("extraction") or {}
            span = None
            if isinstance(extraction, dict):
                start, end = extraction.get("quote_start"), extraction.get("quote_end")
                if (
                    type(start) is int
                    and type(end) is int
                    and row.clean_hash is not None
                    and extraction.get("clean_text_hash") == row.clean_hash
                    and 0 <= start < end <= row.clean_length
                    and end - start == len(quote)
                ):
                    span = [start, end]
            manual = group is not None and group.detection_metadata.get("detector") != "claims-copy"
            snapshots.append(
                {
                    "evidence_id": str(evidence.id),
                    "claim_id": str(claim.id),
                    "document_id": str(row.document_id),
                    "source_id": str(row.source_id),
                    "normalized_claim": normalize(claim.claim_text),
                    "entities": sorted({normalize(value) for value in entities}) or None,
                    **extra_context,
                    "quote": quote,
                    "quote_span": span,
                    "location_id": str(claim.location_id) if claim.location_id else None,
                    "occurred_at": claim.occurred_at.isoformat() if claim.occurred_at else None,
                    "stance": evidence.stance.value,
                    "content_hash": row.content_hash,
                    "clean_hash": row.clean_hash,
                    "url": _url(row.canonical_url),
                    "platform": normalize(row.platform),
                    "author": identity(row.author_identifier),
                    "thread": identity(row.thread_id),
                    "cross_post_of": _url(row.cross_post_of),
                    "vector": _vector(claim),
                    "manual_group_id": str(group.id) if manual else None,
                }
            )
        snapshot = {
            "policy": POLICY,
            "hypothesis_id": str(hypothesis_id),
            "version": VERSION,
            "embedding_model": MODEL,
            "embedding_revision": REVISION,
            "claims": snapshots,
        }
        fingerprint = _hash(snapshot)
        run_id = await session.scalar(
            insert(DuplicateDetectionRun)
            .values(
                hypothesis_id=hypothesis_id,
                version=VERSION,
                input_hash=fingerprint,
                snapshot=snapshot,
            )
            .on_conflict_do_nothing(constraint="uq_duplicate_run")
            .returning(DuplicateDetectionRun.id)
        )
        fresh = run_id is not None
        invalidated = await invalidate_independence(session, hypothesis_id) if fresh else 0
        if run_id is None:
            run_id = await session.scalar(
                select(DuplicateDetectionRun.id).where(
                    DuplicateDetectionRun.hypothesis_id == hypothesis_id,
                    DuplicateDetectionRun.version == VERSION,
                    DuplicateDetectionRun.input_hash == fingerprint,
                )
            )
        pairs, components = await asyncio.to_thread(_assess, snapshots)
        if fresh:
            assessments = [
                {
                    "run_id": run_id,
                    "left_claim_id": UUID(left),
                    "right_claim_id": UUID(right),
                    **assessment,
                }
                for (left, right), assessment in pairs.items()
            ]
            for start in range(0, len(assessments), 200):
                await session.execute(insert(DuplicateAssessment), assessments[start : start + 200])
        assignments, groups, reasons = {}, [], []
        for component in components:
            if any(item["manual_group_id"] for item in component):
                reasons.append("manual_group_preserved")
                continue
            group_fingerprint = _hash(
                {
                    "policy": POLICY,
                    "hypothesis_id": str(hypothesis_id),
                    "version": VERSION,
                    "members": [_hash(item) for item in component],
                }
            )
            group_id = await session.scalar(
                insert(DuplicateGroup)
                .values(
                    description="Confirmed textual copies; not independent reports by default.",
                    fingerprint=group_fingerprint,
                    detection_metadata={
                        "detector": "claims-copy",
                        "version": VERSION,
                        "hypothesis_id": str(hypothesis_id),
                        "first_run_id": str(run_id),
                        "claim_ids": [item["claim_id"] for item in component],
                    },
                )
                .on_conflict_do_nothing(index_elements=[DuplicateGroup.fingerprint])
                .returning(DuplicateGroup.id)
            )
            if group_id is None:
                group_id = await session.scalar(
                    select(DuplicateGroup.id).where(DuplicateGroup.fingerprint == group_fingerprint)
                )
            groups.append(group_id)
            assignments.update({item["evidence_id"]: group_id for item in component})
        changed = 0
        for row, item in zip(rows, snapshots, strict=True):
            if item["manual_group_id"]:
                reasons.append("manual_group_preserved")
                continue
            group_id = assignments.get(item["evidence_id"])
            evidence = row.Evidence
            if evidence.duplicate_group_id != group_id:
                evidence.duplicate_group_id = group_id
                evidence.independence_score = evidence.evidence_weight = None
                evidence.independence_run_id = None
                evidence.independence_components = {}
                changed += 1
        if changed or invalidated:
            hypothesis.confidence_score = hypothesis.prevalence_score = hypothesis.recency_score = (
                None
            )
        by_claim = {item["claim_id"]: item for item in snapshots}
        for (left, right), assessment in pairs.items():
            if assessment["classification"] == "duplicate":
                left_item, right_item = by_claim[left], by_claim[right]
                if assignments.get(left_item["evidence_id"]) is None or assignments.get(
                    left_item["evidence_id"]
                ) != assignments.get(right_item["evidence_id"]):
                    reasons.append("confirmed_pair_needs_group_review")
        await session.flush()
        return DetectionResult(
            run_id=run_id,
            evidence=len(rows),
            assessments=len(pairs),
            suspected=sum(value["classification"] == "suspected" for value in pairs.values()),
            groups=groups,
            changed=changed,
            complete=not reasons,
            review_reasons=sorted(set(reasons)),
        )
