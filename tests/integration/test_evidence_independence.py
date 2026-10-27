import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.db.duplicates import detect_duplicates
from app.db.hypothesis_lifecycle import Draft, Review, split_hypothesis, update_hypothesis
from app.db.independence import calculate_independence
from app.embeddings import text_hash
from app.models import (
    Claim,
    Document,
    DuplicateDetectionRun,
    DuplicateGroup,
    Evidence,
    Hypothesis,
    IndependenceRun,
    Location,
    Source,
)
from app.models.location import LocationType
from app.models.source import SourceType

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]
LONG_QUOTE = (
    "At the station a driver refused the meter and demanded a much higher fixed fare "
    "before taking the traveler to the hotel late that evening."
)


@pytest.fixture
async def setup(session):
    location = Location(
        name="Independence fixture", canonical_name=str(uuid4()), type=LocationType.CITY
    )
    session.add(location)
    await session.flush()
    hypothesis = Hypothesis(
        primary_location_id=location.id,
        title="Reported fares",
        canonical_description="Reports of inflated fares",
        category="transport",
        confidence_score=0.8,
    )
    session.add(hypothesis)
    await session.flush()
    return location, hypothesis


async def report(
    session,
    setup,
    *,
    author=None,
    thread=None,
    published=None,
    metadata=None,
    quote=None,
    raw_hash=None,
    platform="forum",
    document_published=None,
    occurred=None,
):
    source = Source(
        platform=platform,
        canonical_url=f"https://example.org/{uuid4()}",
        author_identifier=author,
        source_type=SourceType.FORUM,
        published_at=published,
        source_metadata={"thread_id": thread, **(metadata or {})},
    )
    session.add(source)
    await session.flush()
    document = Document(
        source_id=source.id,
        raw_object_path=f"raw/{uuid4()}",
        content_hash=raw_hash or text_hash(str(uuid4())),
        published_at=document_published,
    )
    session.add(document)
    await session.flush()
    claim = Claim(
        document_id=document.id,
        location_id=setup[0].id,
        claim_text="A driver demanded an inflated fare.",
        normalized_claim="inflated fare",
        claim_type="transport",
        occurred_at=occurred,
        structured_data={
            "evidence_quote": quote or f"A distinct report {uuid4()}",
            "publication_status": "held",
        },
    )
    session.add(claim)
    await session.flush()
    evidence = Evidence(
        hypothesis_id=setup[1].id,
        claim_id=claim.id,
        stance="supports",
        source_quality_score=0.7,
        evidence_weight=0.6,
    )
    session.add(evidence)
    await session.flush()
    return evidence, claim, document, source


async def test_unknown_authors_remain_partial_with_persisted_components_and_stable_runs(
    session, setup
):
    a, _, _, _ = await report(session, setup, author="anonymous")
    b, _, _, _ = await report(session, setup)
    result = await calculate_independence(session, setup[1].id)
    assert result.complete and result.total_credit == 1 and result.changed == 2
    assert a.independence_score == b.independence_score == 0.5
    assert "author_identity_unknown" in a.independence_components["unknowns"]
    assert a.source_quality_score == 0.7 and a.evidence_weight is None
    assert setup[1].confidence_score is None
    repeated = await calculate_independence(session, setup[1].id)
    assert repeated.run_id == result.run_id and repeated.changed == 0
    assert (
        await session.scalar(
            select(func.count())
            .select_from(IndependenceRun)
            .where(IndependenceRun.hypothesis_id == setup[1].id)
        )
        == 1
    )


async def test_duplicate_group_total_credit_is_bounded_and_publication_holds_stay_held(
    session, setup
):
    items = [await report(session, setup, quote=LONG_QUOTE, raw_hash="a" * 64) for _ in range(3)]
    result = await calculate_independence(session, setup[1].id)
    assert result.total_credit == 0.5
    assert sorted(item[0].independence_score for item in items) == [0, 0, 0.5]
    assert all(item[1].structured_data["publication_status"] == "held" for item in items)
    run = await session.get(IndependenceRun, result.run_id)
    assert run.duplicate_run_id == result.duplicate_run_id and len(run.scores) == 3


async def test_same_author_reports_are_correlated_without_being_declared_duplicate(session, setup):
    a, _, _, _ = await report(
        session, setup, author="u123", published=datetime(2026, 1, 1, tzinfo=UTC)
    )
    b, _, _, _ = await report(
        session, setup, author="u123", published=datetime(2026, 2, 1, tzinfo=UTC)
    )
    await calculate_independence(session, setup[1].id)
    assert a.independence_score == 1 and b.independence_score == 0.25
    assert a.duplicate_group_id is None and b.duplicate_group_id is None


async def test_publication_metadata_change_recalculates_without_changing_duplicate_run(
    session, setup
):
    evidence, _, _, source = await report(session, setup, author="known")
    original = await calculate_independence(session, setup[1].id)
    assert evidence.independence_score == 0.75
    source.published_at = datetime(2026, 1, 1, tzinfo=UTC)
    await session.flush()
    refreshed = await calculate_independence(session, setup[1].id)
    assert (
        refreshed.run_id != original.run_id
        and refreshed.duplicate_run_id == original.duplicate_run_id
    )
    assert evidence.independence_score == 1
    assert (await session.get(IndependenceRun, original.run_id)).scores[str(evidence.id)][
        "score"
    ] == 0.75


async def test_document_publication_time_takes_precedence_and_occurrence_is_not_substituted(
    session, setup
):
    evidence, _, _, _ = await report(
        session,
        setup,
        author="u",
        published=datetime(2020, 1, 1, tzinfo=UTC),
        document_published=datetime(2026, 1, 1, tzinfo=UTC),
        occurred=datetime(2019, 1, 1, tzinfo=UTC),
    )
    result = await calculate_independence(session, setup[1].id)
    inputs = (await session.get(IndependenceRun, result.run_id)).snapshot["evidence"][0]
    assert inputs["published_at"].startswith("2026-01-01")
    assert evidence.independence_components["publication_basis"] == "document"


async def test_unknown_publication_time_stays_unknown_despite_known_occurrence(session, setup):
    evidence, _, _, _ = await report(session, setup, occurred=datetime(2010, 1, 1, tzinfo=UTC))
    await calculate_independence(session, setup[1].id)
    assert "publication_time_unknown" in evidence.independence_components["unknowns"]
    assert evidence.independence_components["ordering_basis"] == "claim_ingestion_time"


async def test_explicit_references_are_directional_even_if_target_is_ordered_later(session, setup):
    original, _, _, source = await report(
        session,
        setup,
        author="original",
        published=datetime(2026, 2, 1, tzinfo=UTC),
        platform="other",
    )
    citing, _, _, _ = await report(
        session,
        setup,
        author="citing",
        published=datetime(2026, 1, 1, tzinfo=UTC),
        metadata={"reference_urls": [source.canonical_url]},
    )
    await calculate_independence(session, setup[1].id)
    assert citing.independence_score == 0.4 and original.independence_score == 1
    assert citing.independence_components["upstream_references"][0]["evidence_id"] == str(
        original.id
    )


async def test_invalid_references_report_partial_scoring_and_unknown_components(session, setup):
    evidence, _, _, _ = await report(
        session,
        setup,
        author="known",
        published=datetime(2026, 1, 1, tzinfo=UTC),
        metadata={"reference_urls": ["not-a-url"]},
    )
    result = await calculate_independence(session, setup[1].id)
    assert not result.complete and "invalid_reference_urls" in result.review_reasons
    assert evidence.independence_score == 0.5


async def test_manual_groups_are_respected_and_pending_review_is_explicit(session, setup):
    group = DuplicateGroup(description="Operator's group")
    session.add(group)
    await session.flush()
    a, _, _, _ = await report(session, setup)
    b, _, _, _ = await report(session, setup)
    a.duplicate_group_id = b.duplicate_group_id = group.id
    await session.flush()
    result = await calculate_independence(session, setup[1].id)
    assert not result.complete and "manual_group_preserved" in result.review_reasons
    assert result.total_credit == 0.5 and a.duplicate_group_id == b.duplicate_group_id == group.id


async def test_refreshed_duplicate_inputs_invalidate_current_cache_but_retain_history(
    session, setup
):
    a, first, _, _ = await report(session, setup, raw_hash="b" * 64)
    b, second, _, _ = await report(session, setup, raw_hash="b" * 64)
    original = await calculate_independence(session, setup[1].id)
    for claim in (first, second):
        claim.structured_data = {**claim.structured_data, "evidence_quote": LONG_QUOTE}
    await session.flush()
    await detect_duplicates(session, setup[1].id)
    assert a.independence_score is None and b.independence_run_id is None
    assert a.independence_components == {}
    assert (await session.get(IndependenceRun, original.run_id)).scores
    recalculated = await calculate_independence(session, setup[1].id)
    assert recalculated.run_id != original.run_id and recalculated.total_credit == 0.5


async def test_split_keeps_archived_score_components_and_clears_current_scope_caches(
    session, setup
):
    a, _, _, _ = await report(session, setup)
    b, _, _, _ = await report(session, setup)
    result = await calculate_independence(session, setup[1].id)
    old_components = a.independence_components
    event = await split_hypothesis(
        session,
        setup[1].id,
        [a.id],
        key=str(uuid4()),
        review=Review(actor="operator", reason="Reviewed selected observation"),
    )
    replacement = await session.get(Evidence, a.superseded_by_id)
    assert a.archived_at and a.independence_run_id == result.run_id
    assert a.independence_components == old_components
    assert replacement.independence_score is None and replacement.independence_run_id is None
    assert b.independence_score is None and b.independence_run_id is None
    child = await calculate_independence(session, event.target_id)
    assert child.evidence == 1 and child.total_credit == 0.5


async def test_metadata_edits_invalidate_versioned_cache(session, setup):
    evidence, _, _, _ = await report(session, setup)
    await calculate_independence(session, setup[1].id)
    await update_hypothesis(
        session,
        setup[1].id,
        Draft(title="Reviewed", description="Reviewed pattern", category="transport"),
        key=str(uuid4()),
        review=Review(actor="operator", reason="Reviewed description"),
    )
    assert evidence.independence_score is None and evidence.independence_components == {}


async def test_reference_limit_failure_does_not_commit_partial_runs(session, setup):
    await report(
        session, setup, metadata={"reference_urls": [f"https://example.org/{i}" for i in range(21)]}
    )
    hypothesis_id = setup[1].id
    with pytest.raises(ValueError, match="reference_limit"):
        await calculate_independence(session, hypothesis_id)
    assert (
        await session.scalar(
            select(func.count())
            .select_from(IndependenceRun)
            .where(IndependenceRun.hypothesis_id == hypothesis_id)
        )
        == 0
    )
    assert (
        await session.scalar(
            select(func.count())
            .select_from(DuplicateDetectionRun)
            .where(DuplicateDetectionRun.hypothesis_id == hypothesis_id)
        )
        == 0
    )


async def test_empty_cohort_is_a_repeatable_zero_credit_run(session, setup):
    result = await calculate_independence(session, setup[1].id)
    assert result.evidence == result.changed == 0 and result.total_credit == 0 and result.complete
    assert setup[1].confidence_score is None
    assert (await calculate_independence(session, setup[1].id)).run_id == result.run_id


async def test_self_crosspost_is_unknown_metadata_not_an_invented_upstream_report(session, setup):
    evidence, _, _, source = await report(
        session, setup, author="known", published=datetime(2026, 1, 1, tzinfo=UTC)
    )
    source.source_metadata = {"cross_post_of": source.canonical_url}
    await session.flush()
    result = await calculate_independence(session, setup[1].id)
    assert not result.complete and evidence.independence_score == 0.5
    assert evidence.independence_components["cross_post_of"] is None
