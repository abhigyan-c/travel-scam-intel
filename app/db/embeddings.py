"""Version-safe embedding persistence and exact pgvector candidate retrieval."""

from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, or_, select, update

from app.db.resolution import _descendants, get_location_path
from app.embeddings import text_hash, validate_vector
from app.models import Claim, Hypothesis


def input_expression(model):
    if model is Claim:
        return Claim.claim_text
    if model is Hypothesis:
        return Hypothesis.title + "\n" + Hypothesis.canonical_description
    raise ValueError("unsupported_embedding_entity")


def input_hash_expression(model):
    return func.encode(func.sha256(func.convert_to(input_expression(model), "UTF8")), "hex")


def current_embedding(model, embedder):
    return (
        model.embedding.is_not(None),
        model.embedding_model == embedder.model,
        model.embedding_revision == embedder.revision,
        model.embedding_input_hash == input_hash_expression(model),
    )


async def backfill_embeddings(session, model, embedder, *, limit: int = 100) -> dict:
    """Bound total work; failed items are reported and retained for a later retry. No commit."""
    if not 1 <= limit <= 1000:
        raise ValueError("embedding_backfill_limit_must_be_1_to_1000")
    expression = input_expression(model)
    stale = or_(
        model.embedding.is_(None),
        model.embedding_model.is_distinct_from(embedder.model),
        model.embedding_revision.is_distinct_from(embedder.revision),
        model.embedding_input_hash.is_distinct_from(input_hash_expression(model)),
    )
    result = {"examined": 0, "updated": 0, "changed": 0, "errors": []}
    cursor = None
    while result["examined"] < limit:
        query = select(model.id, expression.label("input")).where(stale)
        if cursor is not None:
            query = query.where(model.id > cursor)
        rows = (
            await session.execute(
                query.order_by(model.id).limit(min(16, limit - result["examined"]))
            )
        ).all()
        if not rows:
            break
        for identity, content in rows:
            result["examined"] += 1
            try:
                vectors = await embedder.embed([content])
                vector = validate_vector(vectors[0])
            except (ValueError, RuntimeError, OSError) as exc:
                result["errors"].append({"id": str(identity), "code": type(exc).__name__})
                continue
            written = await session.execute(
                update(model)
                .where(
                    model.id == identity,
                    expression == content,
                )
                .values(
                    embedding=vector,
                    embedding_model=embedder.model,
                    embedding_revision=embedder.revision,
                    embedding_input_hash=text_hash(content),
                )
                .execution_options(synchronize_session=False)
            )
            result["updated" if written.rowcount else "changed"] += 1
        cursor = rows[-1].id
    return result


class Candidate(BaseModel):
    hypothesis_id: UUID
    location_id: UUID
    title: str
    similarity: float


async def search_candidates(
    session,
    text: str,
    location_id: UUID,
    embedder,
    *,
    limit: int = 10,
    min_similarity: float = 0,
) -> list[Candidate]:
    if not 1 <= limit <= 50 or not 0 <= min_similarity <= 1:
        raise ValueError("invalid_candidate_search_limits")
    path = await get_location_path(session, location_id)
    descendants = _descendants(location_id)
    vector = validate_vector((await embedder.embed([text]))[0])
    distance = Hypothesis.embedding.cosine_distance(vector)
    query = (
        select(Hypothesis, distance.label("distance"))
        .where(
            *current_embedding(Hypothesis, embedder),
            or_(
                Hypothesis.primary_location_id.in_([row.id for row in path]),
                Hypothesis.primary_location_id.in_(select(descendants.c.id)),
            ),
            distance <= 1 - min_similarity,
        )
        .order_by(distance, Hypothesis.id)
        .limit(limit)
    )
    return [
        Candidate(
            hypothesis_id=row.id,
            location_id=row.primary_location_id,
            title=row.title,
            similarity=max(-1.0, min(1.0, 1 - float(value))),
        )
        for row, value in (await session.execute(query)).all()
    ]


async def search_claim_candidates(session, claim_id: UUID, embedder, *, limit: int = 10):
    claim = await session.get(Claim, claim_id)
    if claim is None:
        raise ValueError("claim_not_found")
    if claim.location_id is None:
        return []
    return await search_candidates(
        session, claim.claim_text, claim.location_id, embedder, limit=limit
    )
