import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.models import (
    Claim,
    Document,
    DuplicateGroup,
    Evidence,
    Hypothesis,
    HypothesisScoreSnapshot,
    Location,
    Source,
    UserReport,
)
from app.models.evidence import EvidenceStance
from app.models.location import LocationType
from app.models.source import SourceType
from app.models.user_report import ReportProvenance

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]


def snapshot(hypothesis_id, /, **changes):
    return HypothesisScoreSnapshot(
        **{
            "hypothesis_id": hypothesis_id,
            "confidence_score": 0.5,
            "prevalence_score": 0.2,
            "recency_score": 1,
            "scoring_version": "fixture-v1",
            "explanation": "Fixture only",
            "components": {"supporting_count": 1},
            **changes,
        }
    )


@pytest.mark.parametrize("provenance", list(ReportProvenance))
async def test_full_report_provenance_graph(session, records, provenance):
    source, document, claims, hypotheses = records
    report = UserReport(
        document_id=document.id, report_text="Fixture report", provenance_class=provenance
    )
    group = DuplicateGroup(description="Same underlying fixture report")
    session.add_all([report, group])
    await session.flush()
    rows = [
        Evidence(
            claim_id=item.id,
            hypothesis_id=hypotheses[0].id,
            stance=stance,
            duplicate_group_id=group.id,
        )
        for item, stance in zip(
            claims[:2], [EvidenceStance.SUPPORTS, EvidenceStance.CONTRADICTS], strict=True
        )
    ]
    session.add_all(rows)
    await session.flush()
    report_id, source_id, document_id, hypothesis_id, group_id = (
        report.id,
        source.id,
        document.id,
        hypotheses[0].id,
        group.id,
    )
    location_id = hypotheses[0].primary_location_id
    session.expunge_all()
    stored_report = await session.scalar(
        select(UserReport)
        .where(UserReport.id == report_id)
        .options(joinedload(UserReport.document).joinedload(Document.source))
    )
    assert stored_report.provenance_class == provenance
    assert stored_report.document.id == document_id
    assert stored_report.document.source.id == source_id
    assert stored_report.document.source.source_type == SourceType.USER_REPORT
    assert stored_report.document.source.author_identifier is None
    assert stored_report.location_id is None and stored_report.occurred_at is None
    assert stored_report.created_at.tzinfo is not None
    evidence = list(
        await session.scalars(
            select(Evidence)
            .where(Evidence.hypothesis_id == hypothesis_id)
            .options(
                joinedload(Evidence.claim).joinedload(Claim.document).joinedload(Document.source),
                joinedload(Evidence.hypothesis),
                joinedload(Evidence.duplicate_group),
            )
        )
    )
    assert {row.stance for row in evidence} == {EvidenceStance.SUPPORTS, EvidenceStance.CONTRADICTS}
    for row in evidence:
        assert row.hypothesis.primary_location_id == location_id
        assert row.claim.document.source.id == source_id
        assert row.duplicate_group.id == group_id
    for model, identity in [
        (Location, location_id),
        (Hypothesis, hypothesis_id),
        (Claim, evidence[0].claim_id),
        (Document, document_id),
        (Source, source_id),
        (DuplicateGroup, group_id),
    ]:
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(delete(model).where(model.id == identity))


@pytest.mark.parametrize(
    "changes",
    [
        {"document_id": None},
        {"document_id": uuid4()},
        {"location_id": uuid4()},
        {"report_text": " "},
        {"provenance_class": "invented"},
        {"provenance_class": None},
    ],
)
async def test_invalid_report_rejected(session, records, changes):
    values = {
        "document_id": records[1].id,
        "report_text": "Fixture",
        "provenance_class": ReportProvenance.WITNESSED,
        **changes,
    }
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(UserReport(**values))
            await session.flush()


async def test_report_document_unique_and_protected_without_claims(session, records):
    session.add(
        UserReport(
            document_id=records[1].id,
            report_text="Fixture",
            provenance_class=ReportProvenance.FIRST_HAND,
        )
    )
    await session.flush()
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                UserReport(
                    document_id=records[1].id,
                    report_text="Repeated",
                    provenance_class=ReportProvenance.SECOND_HAND,
                )
            )
            await session.flush()
    await session.execute(delete(Claim).where(Claim.document_id == records[1].id))
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(delete(Document).where(Document.id == records[1].id))


async def test_history_retained_when_current_scores_change(session, records):
    hypothesis = records[3][0]
    first = snapshot(hypothesis.id)
    second = snapshot(hypothesis.id, confidence_score=0.8, scoring_version="fixture-v2")
    session.add_all([first, second])
    await session.flush()
    first_id, second_id, hypothesis_id = first.id, second.id, hypothesis.id
    hypothesis.confidence_score = 0.9
    await session.flush()
    session.expunge_all()
    first = await session.get(HypothesisScoreSnapshot, first_id)
    second = await session.get(HypothesisScoreSnapshot, second_id)
    assert first.confidence_score == 0.5 and second.confidence_score == 0.8
    assert first.scoring_version == "fixture-v1" and second.scoring_version == "fixture-v2"
    assert first.components == {"supporting_count": 1} and first.explanation == "Fixture only"
    assert first.created_at.tzinfo is not None
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(delete(Hypothesis).where(Hypothesis.id == hypothesis_id))


@pytest.mark.parametrize(
    "changes",
    [
        {"hypothesis_id": None},
        {"hypothesis_id": uuid4()},
        {"confidence_score": None},
        {"confidence_score": -0.1},
        {"confidence_score": 1.1},
        {"prevalence_score": float("nan")},
        {"recency_score": float("inf")},
        {"scoring_version": " "},
        {"explanation": ""},
        {"components": []},
    ],
)
async def test_invalid_snapshot_rejected(session, records, changes):
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(snapshot(records[3][0].id, **changes))
            await session.flush()


async def test_unknown_duplicate_group_rejected(session, records):
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                Evidence(
                    claim_id=records[2][0].id,
                    hypothesis_id=records[3][0].id,
                    stance=EvidenceStance.RELATED,
                    duplicate_group_id=uuid4(),
                )
            )
            await session.flush()


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
