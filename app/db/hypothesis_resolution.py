"""Resolve claims into inspectable decisions without changing hypotheses or evidence."""

import asyncio
import json
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.db.embeddings import current_embedding, search_candidates
from app.db.resolution import _descendants, get_location_path
from app.embeddings import text_hash
from app.hypothesis_resolution import SYSTEM_PROMPT, VERSION, Decision
from app.integrations.sarvam import MODEL
from app.models import Claim, Hypothesis, HypothesisResolution
from app.models.hypothesis import HypothesisStatus


def _claim_snapshot(claim):
    return {
        "id": str(claim.id),
        "text": claim.claim_text,
        "type": claim.claim_type,
        "document_id": str(claim.document_id),
        "occurred_at": claim.occurred_at.isoformat() if claim.occurred_at else None,
        "location_id": str(claim.location_id) if claim.location_id else None,
        "data": {
            key: claim.structured_data.get(key)
            for key in (
                "evidence_quote",
                "location",
                "approach",
                "methods",
                "warning_signs",
                "entities",
                "sensitive",
                "publication_status",
                "occurred_at",
            )
        },
    }


def _candidate_snapshot(row):
    return {
        "id": str(row.id),
        "location_id": str(row.primary_location_id),
        "title": row.title,
        "description": row.canonical_description,
        "category": row.category,
        "status": row.status.value,
    }


def _scope(location_id, path):
    descendants = _descendants(location_id)
    return and_(
        Hypothesis.status != HypothesisStatus.INACTIVE,
        Hypothesis.merged_into_id.is_(None),
        or_(
            Hypothesis.primary_location_id.in_([row.id for row in path]),
            Hypothesis.primary_location_id.in_(select(descendants.c.id)),
        ),
    )


async def _coverage(session, scope, embedder):
    total, current, latest = (
        await session.execute(
            select(
                func.count(Hypothesis.id),
                func.count(Hypothesis.id).filter(and_(*current_embedding(Hypothesis, embedder))),
                func.max(Hypothesis.updated_at),
            ).where(scope)
        )
    ).one()
    return {"total": total, "current_vectors": current, "last_updated": str(latest)}


async def _context(session, claim, embedder, limit):
    path = await get_location_path(session, claim.location_id)
    scope = _scope(claim.location_id, path)
    context = {
        "geography": [
            {"id": str(row.id), "name": row.name, "type": row.type.value} for row in path
        ],
        "coverage": await _coverage(session, scope, embedder),
    }
    exact = (
        await session.scalars(
            select(Hypothesis)
            .where(
                Hypothesis.primary_location_id == claim.location_id,
                Hypothesis.canonical_description == claim.claim_text,
                Hypothesis.status != HypothesisStatus.INACTIVE,
                Hypothesis.merged_into_id.is_(None),
            )
            .order_by(Hypothesis.id)
            .limit(2)
        )
    ).all()
    if exact:
        context["method"] = "exact" if len(exact) == 1 else "ambiguous_exact"
        candidates = exact
    elif context["coverage"]["total"] == 0:
        context["method"], candidates = "empty_scope", []
    elif context["coverage"]["current_vectors"] != context["coverage"]["total"]:
        context["method"], candidates = "incomplete_embeddings", []
    else:
        context["method"] = "semantic"
        found = await search_candidates(
            session, claim.claim_text, claim.location_id, embedder, limit=limit
        )
        identities = [item.hypothesis_id for item in found]
        by_id = {
            row.id: row
            for row in (
                await session.scalars(
                    select(Hypothesis).where(
                        Hypothesis.id.in_(identities),
                    )
                )
            ).all()
        }
        candidates = [by_id[identity] for identity in identities]
        context["similarities"] = {str(item.hypothesis_id): item.similarity for item in found}
    context["candidates"] = [_candidate_snapshot(row) for row in candidates]
    # Full paths let the comparator distinguish city, country and point-of-interest scope.
    for item in context["candidates"]:
        item["geography"] = [
            row.name
            for row in await get_location_path(
                session,
                UUID(item["location_id"]),
            )
        ]
    return context


async def _unchanged(session, claim_id, before, context, embedder):
    claim = await session.scalar(
        select(Claim)
        .where(Claim.id == claim_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if claim is None or _claim_snapshot(claim) != before:
        return False
    if claim.location_id is None:
        return True
    path = await get_location_path(session, claim.location_id)
    if [{"id": str(row.id), "name": row.name, "type": row.type.value} for row in path] != context[
        "geography"
    ]:
        return False
    if await _coverage(session, _scope(claim.location_id, path), embedder) != context["coverage"]:
        return False
    for item in sorted(context["candidates"], key=lambda item: item["id"]):
        row = await session.scalar(
            select(Hypothesis)
            .where(Hypothesis.id == UUID(item["id"]))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or _candidate_snapshot(row) != {
            key: value for key, value in item.items() if key != "geography"
        }:
            return False
        if [
            node.name for node in await get_location_path(session, row.primary_location_id)
        ] != item["geography"]:
            return False
    return True


async def resolve_hypothesis(
    session,
    claim_id: UUID,
    *,
    embedder,
    client,
    max_candidates: int = 6,
    confidence_threshold: float = 0.85,
    timeout: float = 25,
) -> HypothesisResolution:
    if (
        not 1 <= max_candidates <= 10
        or not 0.5 <= confidence_threshold <= 1
        or not 0 < timeout <= 45
    ):
        raise ValueError("invalid_resolver_limits")
    claim = await session.scalar(
        select(Claim).where(Claim.id == claim_id).execution_options(populate_existing=True)
    )
    if claim is None:
        raise ValueError("claim_not_found")
    snapshot = _claim_snapshot(claim)
    details = {
        "claim": snapshot,
        "embedding_model": embedder.model,
        "embedding_revision": embedder.revision,
        "threshold": confidence_threshold,
        "max_candidates": max_candidates,
    }
    context, decision, error, completion = {}, None, None, None
    try:
        async with session.begin_nested():
            async with asyncio.timeout(timeout):
                if claim.location_id is None:
                    context = {"method": "unresolved_location", "candidates": []}
                    error = "unresolved_location"
                else:
                    context = await _context(session, claim, embedder, max_candidates)
                details["context"] = context
                fingerprint = text_hash(
                    json.dumps(
                        {"version": VERSION, "model": MODEL, **details},
                        sort_keys=True,
                    )
                )
                if error is None:
                    cached = await session.scalar(
                        select(HypothesisResolution).where(
                            HypothesisResolution.claim_id == claim_id,
                            HypothesisResolution.input_hash == fingerprint,
                            HypothesisResolution.error.is_(None),
                        )
                    )
                    if cached is not None:
                        if not await _unchanged(session, claim_id, snapshot, context, embedder):
                            raise ValueError("inputs_changed")
                        return cached
                    method = context["method"]
                    if method == "exact":
                        decision = Decision(
                            decision="SAME",
                            target_id=context["candidates"][0]["id"],
                            confidence=1,
                            ambiguous=False,
                            reason="Identical description at the same location.",
                        )
                    elif method == "empty_scope":
                        decision = Decision(
                            decision="NEW",
                            target_id=None,
                            confidence=1,
                            ambiguous=False,
                            reason="No hypotheses in this geographic scope.",
                        )
                    elif method == "incomplete_embeddings":
                        error = "embedding_coverage_incomplete"
                    elif method == "semantic":
                        prompt = json.dumps({"claim": snapshot, **context})
                        if len(prompt) > 30000:
                            error = "context_limit"
                        elif not context["candidates"]:
                            error = "no_retrievable_candidates"
                        else:
                            details["completion"] = {"error": "unfinished", "requests": None}
                            completion = await client.complete(
                                system=SYSTEM_PROMPT, user=prompt, schema=Decision
                            )
                            details["completion"] = completion.model_dump(exclude={"data"})
                            if completion.error or completion.data is None:
                                error = "semantic_provider_failed"
                            else:
                                proposed = Decision.model_validate(completion.data.model_dump())
                                details["proposed"] = proposed.model_dump()
                                targets = {item["id"] for item in context["candidates"]}
                                if (
                                    proposed.target_id is not None
                                    and proposed.target_id not in targets
                                ):
                                    error = "invalid_candidate_target"
                                else:
                                    decision = proposed
                    if not await _unchanged(session, claim_id, snapshot, context, embedder):
                        error, decision = "inputs_changed", None
    except TimeoutError:
        error, decision = "deadline", None
    except (ValueError, RuntimeError, OSError) as exc:
        error, decision = "resolver_input_or_provider_error", None
        details["failure_type"] = type(exc).__name__
    details["context"] = context
    fingerprint = text_hash(
        json.dumps(
            {
                "version": VERSION,
                "model": MODEL,
                **{
                    key: value
                    for key, value in details.items()
                    if key not in {"completion", "proposed", "failure_type"}
                },
            },
            sort_keys=True,
        )
    )
    needs_review = decision is None or bool(error)
    reasons = ["multiple_exact_matches"] if context.get("method") == "ambiguous_exact" else []
    if decision is not None:
        if context.get("method") == "semantic":
            reasons.append("semantic_decision_requires_review")
        if decision.confidence < confidence_threshold or decision.ambiguous:
            reasons.append("low_confidence_or_ambiguous")
        if decision.decision == "CONTRADICTS":
            reasons.append("contradiction_requires_review")
        if decision.decision == "NEW" and context.get("coverage", {}).get("total", 0) > len(
            context["candidates"]
        ):
            reasons.append("candidate_set_truncated")
        target = next(
            (item for item in context["candidates"] if item["id"] == decision.target_id), None
        )
        if target:
            if target["status"] == "inactive":
                reasons.append("inactive_target")
            if decision.decision == "SAME" and target["location_id"] != snapshot["location_id"]:
                reasons.append("geographic_scope_differs")
        if (
            snapshot["data"]["sensitive"]
            or snapshot["data"]["entities"]
            or snapshot["data"]["publication_status"] == "held"
        ):
            reasons.append("sensitive_claim")
    needs_review = needs_review or bool(reasons)
    details["review_reasons"] = reasons
    details["decision"] = decision.model_dump() if decision else None
    values = dict(
        claim_id=claim_id,
        input_hash=fingerprint,
        version=VERSION,
        model=MODEL,
        decision=decision.decision if decision else None,
        target_id=UUID(decision.target_id) if decision and decision.target_id else None,
        confidence=decision.confidence if decision else None,
        needs_review=needs_review,
        error=error,
        details=details,
    )
    statement = insert(HypothesisResolution).values(**values)
    if error is None:
        statement = statement.on_conflict_do_nothing(
            index_elements=[HypothesisResolution.claim_id, HypothesisResolution.input_hash],
            index_where=HypothesisResolution.error.is_(None),
        )
    identity = await session.scalar(statement.returning(HypothesisResolution.id))
    if identity is not None:
        return await session.get(HypothesisResolution, identity)
    return await session.scalar(
        select(HypothesisResolution).where(
            HypothesisResolution.claim_id == claim_id,
            HypothesisResolution.input_hash == fingerprint,
            HypothesisResolution.error.is_(None),
        )
    )
