import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.models import Claim, Document, Evidence, Hypothesis, HypothesisRelation, Location, Source
from app.models.evidence import EvidenceStance
from app.models.hypothesis_relation import HypothesisRelationType
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
    document = Document(source_id=source.id, raw_object_path="raw/fixture", content_hash="b" * 64)
    session.add(document)
    await session.flush()
    claims = [
        Claim(
            document_id=document.id,
            location_id=location.id,
            claim_text=f"Fixture report {index}",
            normalized_claim=f"Fixture report {index}",
            claim_type="overcharging",
        )
        for index in range(4)
    ]
    hypotheses = [
        Hypothesis(
            primary_location_id=location.id,
            title=f"Fixture hypothesis {index}",
            canonical_description=f"Fixture mechanism {index}",
            category="transport",
        )
        for index in range(2)
    ]
    session.add_all([*claims, *hypotheses])
    await session.flush()
    return source, document, claims, hypotheses


def evidence(records, **changes):
    return Evidence(
        **{
            "claim_id": records[2][0].id,
            "hypothesis_id": records[3][0].id,
            "stance": EvidenceStance.SUPPORTS,
            **changes,
        }
    )


def relation(records, **changes):
    return HypothesisRelation(
        **{
            "source_hypothesis_id": records[3][0].id,
            "target_hypothesis_id": records[3][1].id,
            "relation_type": HypothesisRelationType.RELATED_TO,
            **changes,
        }
    )


async def test_all_stances_preserve_provenance_and_unscored_values(session, records):
    source_id, document_id = records[0].id, records[1].id
    hypothesis_id = records[3][0].id
    rows = [
        evidence(records, claim_id=item.id, stance=stance)
        for item, stance in zip(records[2], EvidenceStance, strict=True)
    ]
    session.add_all(rows)
    await session.flush()
    session.expunge_all()
    stored = list(
        await session.scalars(
            select(Evidence)
            .where(Evidence.hypothesis_id == hypothesis_id)
            .options(
                joinedload(Evidence.claim).joinedload(Claim.document).joinedload(Document.source),
                joinedload(Evidence.hypothesis),
            )
        )
    )
    assert len(stored) == 4
    assert {row.stance for row in stored} == set(EvidenceStance)
    for row in stored:
        assert row.claim.document.id == document_id
        assert row.claim.document.source.id == source_id
        assert row.hypothesis.id == hypothesis_id
        assert row.created_at.tzinfo is not None
        assert row.semantic_match_score is None and row.source_quality_score is None
        assert row.independence_score is None and row.evidence_weight is None
        assert row.duplicate_group_id is None


async def test_evidence_scores_roundtrip(session, records):
    row = evidence(
        records,
        semantic_match_score=1,
        source_quality_score=0,
        independence_score=0.75,
        evidence_weight=0.5,
    )
    session.add(row)
    await session.flush()
    identity = row.id
    session.expunge_all()
    stored = await session.get(Evidence, identity)
    assert stored.semantic_match_score == 1 and stored.source_quality_score == 0
    assert stored.independence_score == 0.75 and stored.evidence_weight == 0.5


@pytest.mark.parametrize("stance", list(EvidenceStance))
async def test_duplicate_attachment_rejected_regardless_of_stance(session, records, stance):
    session.add(evidence(records))
    await session.flush()
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(evidence(records, stance=stance))
            await session.flush()
    # A claim can still be evidence for a different hypothesis.
    session.add(evidence(records, hypothesis_id=records[3][1].id, stance=stance))
    await session.flush()


@pytest.mark.parametrize(
    "field",
    [
        "semantic_match_score",
        "source_quality_score",
        "independence_score",
        "evidence_weight",
    ],
)
@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), float("inf")])
async def test_invalid_evidence_score_rejected(session, records, field, value):
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(evidence(records, **{field: value}))
            await session.flush()


@pytest.mark.parametrize(
    "changes",
    [
        {"claim_id": None},
        {"claim_id": uuid4()},
        {"hypothesis_id": None},
        {"hypothesis_id": uuid4()},
        {"stance": None},
        {"stance": "invented"},
    ],
)
async def test_invalid_evidence_reference_or_stance_rejected(session, records, changes):
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(evidence(records, **changes))
            await session.flush()


@pytest.mark.parametrize("kind", list(HypothesisRelationType))
async def test_relations_roundtrip_and_exact_duplicates_rejected(session, records, kind):
    row = relation(records, relation_type=kind)
    session.add(row)
    await session.flush()
    identity, source_id, target_id = row.id, row.source_hypothesis_id, row.target_hypothesis_id
    session.expunge_all()
    stored = await session.scalar(
        select(HypothesisRelation)
        .where(HypothesisRelation.id == identity)
        .options(
            joinedload(HypothesisRelation.source_hypothesis),
            joinedload(HypothesisRelation.target_hypothesis),
        )
    )
    assert stored.relation_type == kind
    assert stored.source_hypothesis.id == source_id
    assert stored.target_hypothesis.id == target_id
    assert stored.created_at.tzinfo is not None
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(relation(records, relation_type=kind))
            await session.flush()


@pytest.mark.parametrize("kind", list(HypothesisRelationType))
async def test_self_relation_rejected_for_every_type(session, records, kind):
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                relation(records, relation_type=kind, target_hypothesis_id=records[3][0].id)
            )
            await session.flush()


@pytest.mark.parametrize(
    "kind",
    [
        HypothesisRelationType.SAME_AS,
        HypothesisRelationType.RELATED_TO,
        HypothesisRelationType.CONTRADICTS,
    ],
)
@pytest.mark.parametrize("reverse_first", [False, True])
async def test_symmetric_reverse_duplicate_rejected(session, records, kind, reverse_first):
    a, b = records[3][0].id, records[3][1].id
    if reverse_first:
        a, b = b, a
    session.add(
        relation(records, source_hypothesis_id=a, target_hypothesis_id=b, relation_type=kind)
    )
    await session.flush()
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                relation(
                    records, source_hypothesis_id=b, target_hypothesis_id=a, relation_type=kind
                )
            )
            await session.flush()


async def test_subtype_is_directional_and_distinct_types_can_coexist(session, records):
    a, b = records[3][0].id, records[3][1].id
    session.add_all(
        [
            relation(records, relation_type=HypothesisRelationType.SUBTYPE_OF),
            relation(
                records,
                relation_type=HypothesisRelationType.SUBTYPE_OF,
                source_hypothesis_id=b,
                target_hypothesis_id=a,
            ),
            relation(records, relation_type=HypothesisRelationType.RELATED_TO),
            relation(records, relation_type=HypothesisRelationType.CONTRADICTS),
        ]
    )
    await session.flush()


@pytest.mark.parametrize(
    "changes",
    [
        {"source_hypothesis_id": None},
        {"source_hypothesis_id": uuid4()},
        {"target_hypothesis_id": None},
        {"target_hypothesis_id": uuid4()},
        {"relation_type": None},
        {"relation_type": "invented"},
    ],
)
async def test_invalid_relation_reference_or_type_rejected(session, records, changes):
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(relation(records, **changes))
            await session.flush()


async def test_evidence_prevents_claim_and_hypothesis_deletion(session, records):
    row = evidence(records, stance=EvidenceStance.CONTRADICTS)
    session.add(row)
    await session.flush()
    for model, identity in [(Claim, row.claim_id), (Hypothesis, row.hypothesis_id)]:
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(delete(model).where(model.id == identity))
    await session.refresh(row)
    assert row.stance == EvidenceStance.CONTRADICTS


async def test_relation_prevents_deleting_either_endpoint(session, records):
    row = relation(records)
    session.add(row)
    await session.flush()
    for identity in (row.source_hypothesis_id, row.target_hypothesis_id):
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(delete(Hypothesis).where(Hypothesis.id == identity))
