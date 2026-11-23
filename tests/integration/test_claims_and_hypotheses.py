import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import joinedload

from app.models import Claim, Document, Hypothesis, Location, Source
from app.models.claim import ClaimStatus
from app.models.hypothesis import HypothesisStatus
from app.models.location import LocationType
from app.models.source import SourceType

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]


@pytest.fixture
async def records(session):
    source = Source(platform="fixture", source_type=SourceType.USER_REPORT)
    location = Location(name="Fixture", canonical_name=str(uuid4()), type=LocationType.CITY)
    session.add_all([source, location])
    await session.flush()
    document = Document(source_id=source.id, raw_object_path="raw/fixture", content_hash="a" * 64)
    session.add(document)
    await session.flush()
    return document.id, source.id, location.id


def claim(document_id, /, **changes):
    return Claim(
        **{
            "document_id": document_id,
            "claim_text": "A traveler reported an inflated fare.",
            "normalized_claim": "Reported inflated fare",
            "claim_type": "overcharging",
            **changes,
        }
    )


def hypothesis(location_id, **changes):
    return Hypothesis(
        **{
            "primary_location_id": location_id,
            "title": "Reported inflated fares",
            "canonical_description": "Travelers report fares above the agreed amount.",
            "category": "transport",
            **changes,
        }
    )


async def test_defaults_and_document_source_lineage(session, records):
    document_id, source_id, location_id = records
    item = claim(document_id, structured_data={"unresolved_location": "unknown stop"})
    pattern = hypothesis(location_id)
    session.add_all([item, pattern])
    await session.flush()
    claim_id, hypothesis_id = item.id, pattern.id
    session.expunge_all()
    stored = await session.scalar(
        select(Claim)
        .where(Claim.id == claim_id)
        .options(joinedload(Claim.document).joinedload(Document.source))
    )
    assert stored.document.source.id == source_id
    assert stored.location_id is None
    assert stored.structured_data == {"unresolved_location": "unknown stop"}
    assert stored.processing_status == ClaimStatus.PENDING
    assert stored.embedding is None and stored.extraction_confidence is None
    assert stored.occurred_at is None
    pattern = await session.get(Hypothesis, hypothesis_id)
    assert pattern.status == HypothesisStatus.CANDIDATE
    assert pattern.primary_location_id == location_id
    assert pattern.first_seen is None and pattern.last_seen is None
    assert pattern.embedding is None
    assert pattern.confidence_score is None
    assert pattern.prevalence_score is None and pattern.recency_score is None
    for row in (stored, pattern):
        assert row.created_at.tzinfo is not None and row.updated_at.tzinfo is not None


async def test_vector_and_observation_roundtrip(session, records):
    document_id, _, location_id = records
    occurred = datetime(2026, 1, 1, tzinfo=UTC)
    vector = [0.25] * 384
    item = claim(
        document_id,
        location_id=location_id,
        embedding=vector,
        occurred_at=occurred,
        extraction_confidence=0.75,
        processing_status=ClaimStatus.PROCESSED,
    )
    pattern = hypothesis(
        location_id,
        embedding=vector,
        first_seen=occurred,
        last_seen=occurred,
        confidence_score=0,
        prevalence_score=1,
        recency_score=0.5,
    )
    session.add_all([item, pattern])
    await session.flush()
    ids = item.id, pattern.id
    session.expunge_all()
    item = await session.get(Claim, ids[0])
    pattern = await session.get(Hypothesis, ids[1])
    assert list(item.embedding) == vector and list(pattern.embedding) == vector
    assert item.occurred_at == occurred and item.extraction_confidence == 0.75
    assert item.location_id == location_id
    assert pattern.first_seen == occurred and pattern.last_seen == occurred
    assert pattern.confidence_score == 0 and pattern.prevalence_score == 1
    assert pattern.recency_score == 0.5


@pytest.mark.parametrize("entity", ["claim", "hypothesis"])
@pytest.mark.parametrize("vector", [[1.0, 0.0], [float("nan")] * 384])
async def test_invalid_vectors_rejected(session, records, entity, vector):
    document_id, _, location_id = records
    row = (
        claim(document_id, embedding=vector)
        if entity == "claim"
        else hypothesis(location_id, embedding=vector)
    )
    with pytest.raises(DBAPIError):
        async with session.begin_nested():
            session.add(row)
            await session.flush()


@pytest.mark.parametrize(
    "changes",
    [
        {"document_id": None},
        {"document_id": uuid4()},
        {"location_id": uuid4()},
        {"claim_text": " "},
        {"normalized_claim": ""},
        {"claim_type": " "},
        {"extraction_confidence": -0.1},
        {"extraction_confidence": 1.1},
        {"extraction_confidence": float("nan")},
        {"structured_data": []},
        {"processing_status": "invented"},
    ],
)
async def test_invalid_claim_rejected(session, records, changes):
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(claim(records[0], **changes))
            await session.flush()


@pytest.mark.parametrize(
    "changes",
    [
        {"primary_location_id": None},
        {"primary_location_id": uuid4()},
        {"title": " "},
        {"canonical_description": ""},
        {"category": " "},
        {"status": "invented"},
        {"confidence_score": -0.1},
        {"prevalence_score": 1.1},
        {"recency_score": float("nan")},
        {"first_seen": datetime(2026, 1, 1, tzinfo=UTC)},
        {"last_seen": datetime(2026, 1, 1, tzinfo=UTC)},
        {
            "first_seen": datetime(2026, 2, 1, tzinfo=UTC),
            "last_seen": datetime(2026, 1, 1, tzinfo=UTC),
        },
    ],
)
async def test_invalid_hypothesis_rejected(session, records, changes):
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(hypothesis(records[2], **changes))
            await session.flush()


async def test_dependencies_cannot_be_deleted(session, records):
    document_id, source_id, location_id = records
    session.add_all([claim(document_id, location_id=location_id), hypothesis(location_id)])
    await session.flush()
    for model, identity in [(Document, document_id), (Source, source_id), (Location, location_id)]:
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(delete(model).where(model.id == identity))


async def test_identical_patterns_retain_separate_locations(session, records):
    other = Location(name="Other", canonical_name=str(uuid4()), type=LocationType.CITY)
    session.add(other)
    await session.flush()
    rows = [hypothesis(records[2]), hypothesis(other.id)]
    session.add_all(rows)
    await session.flush()
    assert rows[0].id != rows[1].id
    assert rows[0].primary_location_id != rows[1].primary_location_id


async def test_database_defaults_support_direct_inserts(session, records):
    row = (
        await session.execute(
            text(
                "INSERT INTO claims (id, document_id, claim_text, normalized_claim, claim_type) "
                "VALUES (:id, :document, 'Reported fare', 'Reported fare', 'overcharging') "
                "RETURNING processing_status, structured_data, embedding"
            ),
            {"id": uuid4(), "document": records[0]},
        )
    ).one()
    assert row.processing_status == "pending" and row.structured_data == {}
    assert row.embedding is None
