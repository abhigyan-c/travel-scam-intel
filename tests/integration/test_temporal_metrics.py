import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.confidence import calculate_confidence
from app.db.frequency import calculate_recency_and_frequency
from app.embeddings import text_hash
from app.models import (
    Claim,
    Document,
    Evidence,
    Hypothesis,
    HypothesisScoreSnapshot,
    Location,
    Source,
    UserReport,
)
from app.models.location import LocationType
from app.models.user_report import ReportProvenance

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]
AS_OF = datetime(2026, 10, 28, 12, tzinfo=UTC)


@pytest.fixture
async def setup(session):
    location = Location(
        name="Temporal fixture", canonical_name=str(uuid4()), type=LocationType.CITY
    )
    session.add(location)
    await session.flush()
    hypothesis = Hypothesis(
        primary_location_id=location.id,
        title="Fare reports",
        canonical_description="Reported meter refusal",
        category="transport",
    )
    session.add(hypothesis)
    await session.flush()
    return location, hypothesis


async def evidence(
    session, setup, *, days=5, source=None, document=None, stance="supports", data=None
):
    if source is None:
        source = Source(
            platform="fixture", source_type="forum", canonical_url=f"https://example.org/{uuid4()}"
        )
        session.add(source)
        await session.flush()
    if document is None:
        document = Document(
            source_id=source.id,
            raw_object_path=f"raw/{uuid4()}",
            content_hash=text_hash(str(uuid4())),
        )
        session.add(document)
        await session.flush()
    claim = Claim(
        document_id=document.id,
        location_id=setup[0].id,
        claim_text="A driver refused the meter",
        normalized_claim="meter refusal",
        claim_type="transport",
        occurred_at=AS_OF - timedelta(days=days) if days is not None else None,
        structured_data={
            "first_hand": True,
            "publication_status": "unreviewed",
            "sensitive": False,
            "evidence_quote": f"Distinct account {uuid4()}",
            **(data or {}),
        },
    )
    session.add(claim)
    await session.flush()
    attachment = Evidence(
        hypothesis_id=setup[1].id, claim_id=claim.id, stance=stance, semantic_match_score=1
    )
    session.add(attachment)
    await session.flush()
    return attachment, claim, source, document


async def test_temporal_snapshot_preserves_confidence_and_reuses_same_reference_time(
    session, setup
):
    await evidence(session, setup)
    before = await calculate_confidence(session, setup[1].id)
    result = await calculate_recency_and_frequency(session, setup[1].id, as_of=AS_OF)
    assert result.components["all_time"] == {
        "raw_report_count": 1,
        "independence_weighted_report_count": 0.5,
    }
    assert result.recency_score == round(2 ** (-5 / 90), 6)
    assert result.prevalence_score == 0.2
    assert setup[1].confidence_score == before.score
    assert result.confidence_snapshot_id == before.snapshot_id
    assert not result.cache_updated
    snapshot = await session.get(HypothesisScoreSnapshot, result.snapshot_id)
    assert snapshot.confidence_score == before.score
    repeated = await calculate_recency_and_frequency(session, setup[1].id, as_of=AS_OF)
    assert repeated.snapshot_id == result.snapshot_id and not repeated.created
    later = await calculate_recency_and_frequency(
        session, setup[1].id, as_of=AS_OF + timedelta(days=90)
    )
    assert later.recency_score < result.recency_score and later.prevalence_score == 0
    assert later.confidence_snapshot_id == before.snapshot_id
    assert snapshot.recency_score == result.recency_score


async def test_occurrence_date_edit_changes_history_without_refreshing_confidence(session, setup):
    _, claim, _, document = await evidence(session, setup, days=100)
    document.published_at = AS_OF
    await session.flush()
    first = await calculate_recency_and_frequency(session, setup[1].id, as_of=AS_OF)
    assert first.components["reporting_windows"]["30"]["raw_report_count"] == 1
    assert first.components["occurrence_windows"]["30"]["raw_report_count"] == 0
    claim.occurred_at = None
    await session.flush()
    unknown = await calculate_recency_and_frequency(session, setup[1].id, as_of=AS_OF)
    assert unknown.recency_score is unknown.prevalence_score is None
    assert not unknown.complete
    assert unknown.snapshot_id != first.snapshot_id


async def test_normalized_report_occurrence_overrides_claim_and_submission_is_separate(
    session, setup
):
    _, claim, _, document = await evidence(session, setup, days=1)
    submitted = UserReport(
        document_id=document.id,
        report_text="A fare report",
        provenance_class=ReportProvenance.FIRST_HAND,
        moderation_status="processed",
        occurred_at=AS_OF - timedelta(days=100),
        created_at=AS_OF,
    )
    session.add(submitted)
    await session.flush()
    first = await calculate_recency_and_frequency(session, setup[1].id, as_of=AS_OF)
    assert first.recency_score == round(2 ** (-100 / 90), 6)
    assert first.components["reporting_windows"]["30"]["raw_report_count"] == 1
    assert first.components["reports"][0]["occurrence_bases"] == ["user_report"]
    submitted.occurred_at = AS_OF
    await session.flush()
    second = await calculate_recency_and_frequency(session, setup[1].id, as_of=AS_OF)
    assert second.recency_score == 1 and second.snapshot_id != first.snapshot_id
    assert claim.occurred_at != submitted.occurred_at


async def test_multiple_claims_and_source_versions_count_once(session, setup):
    _, _, source, document = await evidence(session, setup)
    await evidence(session, setup, source=source, document=document)
    await evidence(session, setup, source=source)
    result = await calculate_recency_and_frequency(session, setup[1].id, as_of=AS_OF)
    assert result.components["all_time"]["raw_report_count"] == 1
    assert result.components["all_time"]["independence_weighted_report_count"] == 0.5
    assert len(result.components["reports"][0]["evidence_ids"]) == 3


async def test_confirmed_copies_increase_raw_count_but_not_independent_frequency(session, setup):
    quote = (
        "A driver at the station refused the meter and demanded a much higher fixed fare "
        "before taking the traveler to a hotel late that evening."
    )
    for _ in range(2):
        _, _, _, document = await evidence(session, setup, data={"evidence_quote": quote})
        document.content_hash = "c" * 64
    await session.flush()
    result = await calculate_recency_and_frequency(session, setup[1].id, as_of=AS_OF)
    assert result.components["all_time"] == {
        "raw_report_count": 2,
        "independence_weighted_report_count": 0.5,
    }
    assert result.components["occurrence_windows"]["30"] == result.components["all_time"]
    assert result.prevalence_score == 0.2


async def test_holds_contradictions_and_other_locations_do_not_inflate_frequency(session, setup):
    await evidence(session, setup)
    await evidence(session, setup, data={"sensitive": True})
    await evidence(session, setup, stance="contradicts")
    _, claim, _, _ = await evidence(session, setup)
    claim.location_id = None
    await session.flush()
    result = await calculate_recency_and_frequency(session, setup[1].id, as_of=AS_OF)
    assert result.components["all_time"]["raw_report_count"] == 1
    assert len(result.components["excluded_evidence"]) == 3


async def test_current_calculation_updates_cache_but_dated_replay_does_not_replace_it(
    session, setup
):
    _, claim, _, _ = await evidence(session, setup)
    claim.occurred_at = datetime.now(UTC) - timedelta(days=5)
    await session.flush()
    current = await calculate_recency_and_frequency(session, setup[1].id)
    assert current.cache_updated
    assert setup[1].recency_score == current.recency_score
    cached = setup[1].recency_score, setup[1].prevalence_score
    historical = await calculate_recency_and_frequency(
        session, setup[1].id, as_of=current.as_of + timedelta(days=365)
    )
    assert not historical.cache_updated
    assert (setup[1].recency_score, setup[1].prevalence_score) == cached


async def test_frequency_preview_rolls_back_snapshots_and_all_scores(session, setup):
    await evidence(session, setup)
    async with session.begin_nested() as preview:
        result = await calculate_recency_and_frequency(session, setup[1].id)
        assert result.cache_updated
        await preview.rollback()
    await session.refresh(setup[1])
    assert setup[1].confidence_score is setup[1].recency_score is setup[1].prevalence_score is None
    assert await session.get(HypothesisScoreSnapshot, result.snapshot_id) is None
    assert not (
        await session.scalars(
            select(HypothesisScoreSnapshot).where(
                HypothesisScoreSnapshot.hypothesis_id == setup[1].id
            )
        )
    ).all()
