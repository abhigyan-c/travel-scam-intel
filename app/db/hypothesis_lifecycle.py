"""Small transactional lifecycle helpers. Callers own commits and operator authentication."""

import json
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.db.evidence import invalidate_independence
from app.db.hypothesis_resolution import _unchanged
from app.embeddings import text_hash
from app.hypothesis_resolution import VERSION, public_new_candidate
from app.models import (
    Claim,
    Evidence,
    Hypothesis,
    HypothesisLifecycleEvent,
    HypothesisRelation,
    HypothesisResolution,
)
from app.models.evidence import EvidenceStance
from app.models.hypothesis import HypothesisStatus
from app.models.hypothesis_relation import HypothesisRelationType

LIFECYCLE_VERSION = "lifecycle-v2"


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    category: str = Field(min_length=1, max_length=100)


def draft_from_claim(claim: Claim) -> Draft:
    text = " ".join(claim.claim_text.split())
    return Draft(
        title=text if len(text) <= 120 else text[:117].rstrip() + "...",
        description=text,
        category=claim.claim_type,
    )


async def _reserve(session, key, operation, payload, review):
    if not isinstance(key, str) or not key.strip() or len(key) > 200:
        raise ValueError("invalid_lifecycle_key")
    if not isinstance(review, Review):
        raise ValueError("review_identity_and_reason_required")
    fingerprint = text_hash(
        json.dumps(
            {"operation": operation, "payload": payload, "review": review.model_dump()},
            sort_keys=True,
            default=str,
        )
    )
    identity = await session.scalar(
        insert(HypothesisLifecycleEvent)
        .values(
            key=key,
            input_hash=fingerprint,
            operation=operation,
            actor=review.actor,
            reason=review.reason,
            details={},
        )
        .on_conflict_do_nothing(index_elements=[HypothesisLifecycleEvent.key])
        .returning(HypothesisLifecycleEvent.id)
    )
    if identity is not None:
        return await session.get(HypothesisLifecycleEvent, identity), True
    previous = await session.scalar(
        select(HypothesisLifecycleEvent).where(
            HypothesisLifecycleEvent.key == key,
        )
    )
    if previous.input_hash != fingerprint:
        raise ValueError("lifecycle_key_conflict")
    return previous, False


async def _lock(session, *identities):
    rows = (
        await session.scalars(
            select(Hypothesis)
            .where(Hypothesis.id.in_(identities))
            .order_by(Hypothesis.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    if len(rows) != len(set(identities)):
        raise ValueError("hypothesis_not_found")
    return {row.id: row for row in rows}


def _current(row):
    if row.merged_into_id is not None or row.status == HypothesisStatus.INACTIVE:
        raise ValueError("hypothesis_not_current")


def _snapshot(row):
    return {
        key: (
            value.isoformat()
            if isinstance(value, datetime)
            else str(value)
            if isinstance(value, UUID)
            else value
        )
        for key in (
            "id",
            "primary_location_id",
            "title",
            "canonical_description",
            "category",
            "status",
            "merged_into_id",
            "first_seen",
            "last_seen",
            "confidence_score",
            "prevalence_score",
            "recency_score",
            "embedding_model",
            "embedding_revision",
            "embedding_input_hash",
        )
        for value in [getattr(row, key)]
    }


def _reset_scores(row):
    row.confidence_score = row.prevalence_score = row.recency_score = None


async def _refresh_dates(session, row):
    await session.flush()
    await invalidate_independence(session, row.id)
    row.first_seen, row.last_seen = (
        await session.execute(
            select(
                func.min(Claim.created_at),
                func.max(Claim.created_at),
            )
            .join(Evidence, Evidence.claim_id == Claim.id)
            .where(
                Evidence.hypothesis_id == row.id,
                Evidence.archived_at.is_(None),
                Evidence.stance == EvidenceStance.SUPPORTS,
            )
        )
    ).one()
    _reset_scores(row)


async def _new(session, location_id, draft):
    row = Hypothesis(
        primary_location_id=location_id,
        title=draft.title,
        canonical_description=draft.description,
        category=draft.category,
        status=HypothesisStatus.CANDIDATE,
    )
    session.add(row)
    await session.flush()
    return row


async def _attach(session, hypothesis_id, claim_id, stance, **scores):
    existing = await session.scalar(
        select(Evidence)
        .where(
            Evidence.hypothesis_id == hypothesis_id,
            Evidence.claim_id == claim_id,
        )
        .with_for_update()
    )
    if existing is not None:
        if existing.archived_at is not None or existing.stance != stance:
            raise ValueError("evidence_attachment_conflict")
        return existing
    row = Evidence(hypothesis_id=hypothesis_id, claim_id=claim_id, stance=stance, **scores)
    session.add(row)
    await session.flush()
    return row


async def _link(session, source_id, target_id, kind):
    if source_id == target_id:
        raise ValueError("self_relationship")
    if kind == HypothesisRelationType.SUBTYPE_OF:
        # Serialize graph reachability checks, including disjoint concurrent edge insertions.
        await session.execute(select(func.pg_advisory_xact_lock(18001)))
        reachable = select(Hypothesis.id).where(Hypothesis.id == target_id).cte(recursive=True)
        reachable = reachable.union(
            select(HypothesisRelation.target_hypothesis_id)
            .join(
                reachable,
                HypothesisRelation.source_hypothesis_id == reachable.c.id,
            )
            .where(HypothesisRelation.relation_type == HypothesisRelationType.SUBTYPE_OF)
        )
        if await session.scalar(select(reachable.c.id).where(reachable.c.id == source_id)):
            raise ValueError("subtype_cycle")
    elif source_id > target_id:
        source_id, target_id = target_id, source_id
    # Legacy symmetric edges may have been stored in either direction.
    matching = [
        HypothesisRelation.source_hypothesis_id == source_id,
        HypothesisRelation.target_hypothesis_id == target_id,
    ]
    if kind != HypothesisRelationType.SUBTYPE_OF:
        matching = [
            or_(
                (HypothesisRelation.source_hypothesis_id == source_id)
                & (HypothesisRelation.target_hypothesis_id == target_id),
                (HypothesisRelation.source_hypothesis_id == target_id)
                & (HypothesisRelation.target_hypothesis_id == source_id),
            )
        ]
    existing = await session.scalar(
        select(HypothesisRelation.id).where(
            *matching,
            HypothesisRelation.relation_type == kind,
        )
    )
    if existing is not None:
        return existing
    return await session.scalar(
        insert(HypothesisRelation)
        .values(
            source_hypothesis_id=source_id,
            target_hypothesis_id=target_id,
            relation_type=kind,
        )
        .returning(HypothesisRelation.id)
    )


async def apply_resolution(session, resolution_id: UUID, *, embedder, review: Review | None = None):
    """Apply an unchanged decision; ordinary NEW candidates need no operator approval."""
    async with session.begin_nested():
        event, fresh = await _reserve(
            session,
            f"resolution:{resolution_id}",
            "apply",
            {"resolution_id": resolution_id},
            review
            or Review(
                actor="system", reason="Apply eligible candidate or deterministic resolution"
            ),
        )
        if not fresh:
            return event
        resolution = await session.scalar(
            select(HypothesisResolution)
            .where(
                HypothesisResolution.id == resolution_id,
            )
            .with_for_update()
        )
        if resolution is None or resolution.error or resolution.decision is None:
            raise ValueError("resolution_not_applicable")
        if resolution.version != VERSION:
            raise ValueError("resolution_version_changed")
        if (
            resolution.needs_review
            or (
                resolution.details.get("context", {}).get("method") == "semantic"
                and not public_new_candidate(
                    resolution.details.get("decision"), resolution.details.get("claim", {})
                )
            )
        ) and review is None:
            raise ValueError("resolution_requires_review")
        details = resolution.details
        if (
            details.get("embedding_model") != embedder.model
            or details.get("embedding_revision") != embedder.revision
        ):
            raise ValueError("resolution_embedding_version_changed")
        if not await _unchanged(
            session, resolution.claim_id, details["claim"], details["context"], embedder
        ):
            raise ValueError("resolution_inputs_changed")
        claim = await session.get(Claim, resolution.claim_id)
        if claim.location_id is None:
            raise ValueError("claim_location_unresolved")
        decision = resolution.decision
        target = None
        if resolution.target_id:
            target = (await _lock(session, resolution.target_id))[resolution.target_id]
            _current(target)
        if decision == "SAME" and target.primary_location_id != claim.location_id:
            raise ValueError("same_requires_same_location")
        if decision in {"NEW", "RELATED", "SUBTYPE"}:
            row = await _new(session, claim.location_id, draft_from_claim(claim))
            evidence = await _attach(
                session,
                row.id,
                claim.id,
                EvidenceStance.SUPPORTS,
                semantic_match_score=resolution.confidence,
            )
            if target is not None:
                await _link(
                    session,
                    row.id,
                    target.id,
                    HypothesisRelationType.SUBTYPE_OF
                    if decision == "SUBTYPE"
                    else HypothesisRelationType.RELATED_TO,
                )
        else:
            row = target
            evidence = await _attach(
                session,
                row.id,
                claim.id,
                EvidenceStance.CONTRADICTS
                if decision == "CONTRADICTS"
                else EvidenceStance.SUPPORTS,
                semantic_match_score=resolution.confidence,
            )
        await _refresh_dates(session, row)
        event.resolution_id, event.source_id, event.target_id = (
            resolution.id,
            resolution.target_id,
            row.id,
        )
        event.details = {
            "version": LIFECYCLE_VERSION,
            "decision": decision,
            "claim_id": str(claim.id),
            "evidence_id": str(evidence.id),
            "after": _snapshot(row),
            "reviewed": review is not None,
        }
        await session.flush()
        return event


async def update_hypothesis(
    session, hypothesis_id: UUID, draft: Draft, *, key: str, review: Review
):
    async with session.begin_nested():
        event, fresh = await _reserve(
            session, key, "update", {"id": hypothesis_id, "draft": draft.model_dump()}, review
        )
        if not fresh:
            return event
        row = (await _lock(session, hypothesis_id))[hypothesis_id]
        _current(row)
        before = _snapshot(row)
        row.title, row.canonical_description, row.category = (
            draft.title,
            draft.description,
            draft.category,
        )
        row.embedding = row.embedding_model = row.embedding_revision = row.embedding_input_hash = (
            None
        )
        _reset_scores(row)
        await invalidate_independence(session, row.id)
        event.source_id = event.target_id = row.id
        event.details = {"version": LIFECYCLE_VERSION, "before": before, "after": _snapshot(row)}
        await session.flush()
        return event


async def _transfer(session, evidence, target_id):
    scores = {
        key: getattr(evidence, key)
        for key in (
            "semantic_match_score",
            "source_quality_score",
            "independence_score",
            "evidence_weight",
            "duplicate_group_id",
        )
    }
    if evidence.independence_run_id is not None:
        scores["independence_score"] = scores["evidence_weight"] = None
    copied = await _attach(
        session,
        target_id,
        evidence.claim_id,
        evidence.stance,
        **scores,
    )
    evidence.archived_at, evidence.superseded_by_id = datetime.now(UTC), copied.id
    return {
        "original_id": str(evidence.id),
        "replacement_id": str(copied.id),
        "claim_id": str(evidence.claim_id),
    }


async def merge_hypotheses(session, source_id: UUID, target_id: UUID, *, key: str, review: Review):
    async with session.begin_nested():
        event, fresh = await _reserve(
            session, key, "merge", {"source": source_id, "target": target_id}, review
        )
        if not fresh:
            return event
        if source_id == target_id:
            raise ValueError("cannot_merge_self")
        rows = await _lock(session, source_id, target_id)
        source, target = rows[source_id], rows[target_id]
        _current(source)
        _current(target)
        if source.primary_location_id != target.primary_location_id:
            raise ValueError("merge_requires_same_location")
        evidence = (
            await session.scalars(
                select(Evidence)
                .where(
                    Evidence.hypothesis_id == source_id,
                    Evidence.archived_at.is_(None),
                )
                .order_by(Evidence.id)
                .limit(1001)
                .with_for_update()
            )
        ).all()
        if len(evidence) > 1000:
            raise ValueError("merge_evidence_limit")
        before = {"source": _snapshot(source), "target": _snapshot(target)}
        transfers = [await _transfer(session, item, target_id) for item in evidence]
        source.merged_into_id, source.status = target_id, HypothesisStatus.INACTIVE
        relation_id = await _link(session, source_id, target_id, HypothesisRelationType.SAME_AS)
        await _refresh_dates(session, source)
        await _refresh_dates(session, target)
        event.source_id, event.target_id = source_id, target_id
        event.details = {
            "version": LIFECYCLE_VERSION,
            "before": before,
            "transfers": transfers,
            "relation_id": str(relation_id),
            "after": {"source": _snapshot(source), "target": _snapshot(target)},
        }
        await session.flush()
        return event


async def move_evidence(session, source_id, target_id, evidence_ids, *, key, review):
    """Archive/recreate selected attachments; never rewrite a claim's geography or history."""
    if not 1 <= len(evidence_ids) <= 100 or len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("move_requires_unique_evidence")
    if source_id == target_id:
        raise ValueError("cannot_move_to_self")
    async with session.begin_nested():
        event, fresh = await _reserve(
            session,
            key,
            "move",
            {"source": source_id, "target": target_id, "evidence": sorted(evidence_ids)},
            review,
        )
        if not fresh:
            return event
        rows = await _lock(session, source_id, target_id)
        source, target = rows[source_id], rows[target_id]
        _current(source)
        _current(target)
        if source.primary_location_id != target.primary_location_id:
            raise ValueError("move_requires_same_location")
        selected = list(
            await session.scalars(
                select(Evidence)
                .where(
                    Evidence.hypothesis_id == source_id,
                    Evidence.id.in_(evidence_ids),
                    Evidence.archived_at.is_(None),
                )
                .order_by(Evidence.id)
                .with_for_update()
            )
        )
        if len(selected) != len(evidence_ids):
            raise ValueError("evidence_not_current_or_not_owned")
        before = {"source": _snapshot(source), "target": _snapshot(target)}
        transfers = [await _transfer(session, item, target_id) for item in selected]
        await _refresh_dates(session, source)
        await _refresh_dates(session, target)
        if source.first_seen is None:
            source.status = HypothesisStatus.INACTIVE
        event.source_id, event.target_id = source_id, target_id
        event.details = {
            "version": LIFECYCLE_VERSION,
            "before": before,
            "transfers": transfers,
            "after": {"source": _snapshot(source), "target": _snapshot(target)},
        }
        await session.flush()
        return event


async def split_hypothesis(
    session,
    source_id: UUID,
    evidence_ids: list[UUID],
    *,
    key: str,
    review: Review,
    draft: Draft | None = None,
    relation_type: HypothesisRelationType = HypothesisRelationType.RELATED_TO,
):
    if not 1 <= len(evidence_ids) <= 100 or len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("split_requires_1_to_100_unique_evidence_ids")
    if relation_type not in {HypothesisRelationType.RELATED_TO, HypothesisRelationType.SUBTYPE_OF}:
        raise ValueError("invalid_split_relationship")
    async with session.begin_nested():
        event, fresh = await _reserve(
            session,
            key,
            "split",
            {
                "source": source_id,
                "evidence_ids": sorted(evidence_ids),
                "relation": relation_type,
                "draft": draft.model_dump() if draft else None,
            },
            review,
        )
        if not fresh:
            return event
        source = (await _lock(session, source_id))[source_id]
        _current(source)
        selected = (
            await session.scalars(
                select(Evidence)
                .where(
                    Evidence.hypothesis_id == source_id,
                    Evidence.id.in_(evidence_ids),
                    Evidence.archived_at.is_(None),
                )
                .order_by(Evidence.id)
                .with_for_update()
            )
        ).all()
        if len(selected) != len(evidence_ids):
            raise ValueError("split_evidence_not_current_or_not_owned")
        seed = next((item for item in selected if item.stance == EvidenceStance.SUPPORTS), None)
        if seed is None:
            raise ValueError("split_requires_supporting_evidence")
        claim = await session.get(Claim, seed.claim_id)
        before = _snapshot(source)
        child = await _new(session, source.primary_location_id, draft or draft_from_claim(claim))
        transfers = [await _transfer(session, item, child.id) for item in selected]
        relation_id = await _link(session, child.id, source_id, relation_type)
        await _refresh_dates(session, child)
        await _refresh_dates(session, source)
        if source.first_seen is None:
            source.status = HypothesisStatus.INACTIVE
        event.source_id, event.target_id = source_id, child.id
        event.details = {
            "version": LIFECYCLE_VERSION,
            "before": before,
            "transfers": transfers,
            "relation_id": str(relation_id),
            "after": {"source": _snapshot(source), "child": _snapshot(child)},
        }
        await session.flush()
        return event


async def link_hypotheses(
    session,
    source_id: UUID,
    target_id: UUID,
    kind: HypothesisRelationType,
    *,
    key: str,
    review: Review,
):
    kind = HypothesisRelationType(kind)
    if kind == HypothesisRelationType.SAME_AS:
        raise ValueError("use_merge_for_same_as")
    async with session.begin_nested():
        event, fresh = await _reserve(
            session, key, "link", {"source": source_id, "target": target_id, "kind": kind}, review
        )
        if not fresh:
            return event
        rows = await _lock(session, source_id, target_id)
        for row in rows.values():
            _current(row)
        relation_id = await _link(session, source_id, target_id, kind)
        event.source_id, event.target_id = source_id, target_id
        event.details = {
            "version": LIFECYCLE_VERSION,
            "relation_id": str(relation_id),
            "kind": kind,
        }
        await session.flush()
        return event


async def canonical_hypothesis(session, hypothesis_id: UUID):
    seen = set()
    for _ in range(32):
        if hypothesis_id in seen:
            raise ValueError("merged_hypothesis_cycle")
        seen.add(hypothesis_id)
        row = await session.get(Hypothesis, hypothesis_id)
        if row is None:
            raise ValueError("hypothesis_not_found")
        if row.merged_into_id is None:
            return row
        hypothesis_id = row.merged_into_id
    raise ValueError("merged_hypothesis_depth")


async def get_relationships(
    session, hypothesis_id: UUID, *, limit: int = 50, after: UUID | None = None
):
    if not 1 <= limit <= 100:
        raise ValueError("invalid_relationship_limit")
    canonical = await canonical_hypothesis(session, hypothesis_id)
    aliases = select(Hypothesis.id).where(Hypothesis.id == canonical.id).cte(recursive=True)
    aliases = aliases.union(
        select(Hypothesis.id).join(aliases, Hypothesis.merged_into_id == aliases.c.id)
    )
    query = select(HypothesisRelation).where(
        or_(
            HypothesisRelation.source_hypothesis_id.in_(select(aliases.c.id)),
            HypothesisRelation.target_hypothesis_id.in_(select(aliases.c.id)),
        )
    )
    if after is not None:
        query = query.where(HypothesisRelation.id > after)
    return (await session.scalars(query.order_by(HypothesisRelation.id).limit(limit))).all()
