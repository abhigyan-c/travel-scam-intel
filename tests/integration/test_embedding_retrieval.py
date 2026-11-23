import os
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.embeddings import backfill_embeddings, search_candidates, search_claim_candidates
from app.embeddings import text_hash
from app.models import Claim, Document, Hypothesis, Location, Source
from app.models.location import LocationType
from app.models.source import SourceType

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]
VECTOR = [1.0] + [0.0] * 383


@pytest.fixture
def embedder():
    client = AsyncMock()
    client.model, client.revision = "fixture-model", "fixture-revision"
    client.embed.return_value = [VECTOR]
    return client


@pytest.fixture
async def hierarchy(session):
    def node(name, parent=None, kind=LocationType.CITY):
        return Location(
            id=uuid4(),
            name=name,
            canonical_name=str(uuid4()),
            type=kind,
            parent_id=parent.id if parent else None,
        )

    country = node("Country", kind=LocationType.COUNTRY)
    city = node("City", country)
    poi = node("POI", city, LocationType.POINT_OF_INTEREST)
    sibling = node("Other city", country)
    foreign = node("Foreign", kind=LocationType.COUNTRY)
    for row in (country, city, poi, sibling, foreign):
        session.add(row)
        await session.flush()
    return country, city, poi, sibling, foreign


def pattern(location, embedder, **changes):
    title, description = "Reported taxi fare scam", "Driver demands an inflated fare."
    return Hypothesis(
        primary_location_id=location.id,
        title=title,
        canonical_description=description,
        category="transport",
        embedding=VECTOR,
        embedding_model=embedder.model,
        embedding_revision=embedder.revision,
        embedding_input_hash=text_hash(title + "\n" + description),
        **changes,
    )


async def test_geographic_scope_includes_ancestors_and_descendants_but_not_siblings(
    session,
    hierarchy,
    embedder,
):
    rows = [pattern(place, embedder) for place in hierarchy]
    session.add_all(rows)
    await session.flush()
    found = await search_candidates(session, "Taxi overcharge", hierarchy[1].id, embedder)
    assert {item.hypothesis_id for item in found} == {row.id for row in rows[:3]}
    assert all(item.similarity == pytest.approx(1) for item in found)
    found = await search_candidates(session, "Taxi overcharge", hierarchy[0].id, embedder, limit=2)
    assert len(found) == 2
    assert [item.hypothesis_id for item in found] == sorted(row.id for row in rows[:4])[:2]


async def test_old_unknown_and_edited_vectors_are_excluded(session, hierarchy, embedder):
    rows = [pattern(hierarchy[1], embedder) for _ in range(5)]
    rows[1].embedding_revision = "old-model"
    rows[2].embedding_model = None
    rows[3].canonical_description = "Edited after embedding"
    rows[4].embedding = None
    session.add_all(rows)
    await session.flush()
    found = await search_candidates(session, "taxi", hierarchy[1].id, embedder)
    assert [item.hypothesis_id for item in found] == [rows[0].id]


async def test_backfill_is_repeatable_and_updates_changed_text(session, hierarchy, embedder):
    rows = [pattern(hierarchy[1], embedder) for _ in range(2)]
    for row in rows:
        row.embedding_revision = "outdated"
    session.add_all(rows)
    await session.flush()
    first = await backfill_embeddings(session, Hypothesis, embedder, limit=1)
    assert first["examined"] == first["updated"] == 1
    second = await backfill_embeddings(session, Hypothesis, embedder, limit=1000)
    assert second["updated"] == 1
    count = embedder.embed.call_count
    assert (await backfill_embeddings(session, Hypothesis, embedder))["updated"] == 0
    assert embedder.embed.call_count == count
    await session.refresh(rows[0])
    rows[0].title = "Changed title"
    await session.flush()
    assert (await backfill_embeddings(session, Hypothesis, embedder))["updated"] == 1


async def test_claim_embedding_and_unresolved_claim_never_searches_world(session, embedder):
    source = Source(platform="fixture", source_type=SourceType.USER_REPORT)
    session.add(source)
    await session.flush()
    document = Document(source_id=source.id, raw_object_path="raw/fixture", content_hash="b" * 64)
    session.add(document)
    await session.flush()
    claim = Claim(
        document_id=document.id,
        claim_text="Taxi fare report",
        normalized_claim="taxi fare report",
        claim_type="transport",
    )
    session.add(claim)
    await session.flush()
    result = await backfill_embeddings(session, Claim, embedder, limit=1000)
    assert result["updated"] >= 1
    await session.refresh(claim)
    assert claim.embedding_input_hash == text_hash(claim.claim_text)
    embedder.embed.reset_mock()
    assert await search_claim_candidates(session, claim.id, embedder) == []
    embedder.embed.assert_not_called()


async def test_failed_embedding_does_not_overwrite_existing_vector(session, hierarchy, embedder):
    row = pattern(hierarchy[1], embedder)
    row.embedding_revision = "old"
    session.add(row)
    await session.flush()
    embedder.embed.side_effect = ValueError("token window")
    result = await backfill_embeddings(session, Hypothesis, embedder)
    assert result["updated"] == 0 and result["errors"]
    assert (
        await session.scalar(select(Hypothesis.embedding_revision).where(Hypothesis.id == row.id))
    ) == "old"


async def test_edit_during_generation_does_not_store_a_stale_vector(session, hierarchy, embedder):
    row = pattern(hierarchy[1], embedder)
    row.embedding_revision = "old"
    session.add(row)
    await session.flush()

    async def generate(texts):
        row.title = "Concurrent edit"
        await session.flush()
        return [VECTOR]

    embedder.embed.side_effect = generate
    result = await backfill_embeddings(session, Hypothesis, embedder)
    assert result["changed"] == 1 and result["updated"] == 0
