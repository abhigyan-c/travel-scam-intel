import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.db.duplicates import detect_duplicates
from app.db.hypothesis_lifecycle import Review, split_hypothesis
from app.embeddings import MODEL, REVISION, text_hash
from app.models import (
    Claim,
    Document,
    DuplicateAssessment,
    DuplicateDetectionRun,
    DuplicateGroup,
    Evidence,
    Hypothesis,
    Location,
    Source,
)
from app.models.location import LocationType
from app.models.source import SourceType

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]
QUOTE = (
    "At the station a driver refused the meter and demanded a much higher fixed fare "
    "before taking the traveler to the hotel late that evening."
)
SUMMARY = "A driver refused the meter and demanded an inflated fixed fare."


@pytest.fixture
async def setup(session):
    location = Location(name="Fixture city", canonical_name=str(uuid4()), type=LocationType.CITY)
    session.add(location)
    await session.flush()
    hypothesis = Hypothesis(
        primary_location_id=location.id,
        title="Reported meter refusals",
        canonical_description=SUMMARY,
        category="transport",
        confidence_score=0.9,
    )
    session.add(hypothesis)
    await session.flush()
    return location, hypothesis


async def report(
    session,
    setup,
    *,
    document=None,
    text=SUMMARY,
    quote=QUOTE,
    author=None,
    thread=None,
    raw_hash=None,
    clean=None,
    vector=False,
    group=None,
):
    if document is None:
        source = Source(
            platform="reddit",
            canonical_url=f"https://example.org/{uuid4()}",
            author_identifier=author,
            source_type=SourceType.FORUM,
            source_metadata={"thread_id": thread} if thread else {},
        )
        session.add(source)
        await session.flush()
        content = clean if clean is not None else str(uuid4()) + "\n" + quote
        document = Document(
            source_id=source.id,
            raw_object_path=f"raw/{uuid4()}",
            content_hash=raw_hash or text_hash(str(uuid4())),
            clean_text=content,
            clean_text_hash=text_hash(content),
        )
        session.add(document)
        await session.flush()
    claim = Claim(
        document_id=document.id,
        location_id=setup[0].id,
        claim_text=text,
        normalized_claim=text.casefold(),
        claim_type="transport",
        structured_data={"evidence_quote": quote, "publication_status": "held"},
    )
    if vector:
        claim.embedding, claim.embedding_model, claim.embedding_revision = (
            [1.0] + [0.0] * 383,
            MODEL,
            REVISION,
        )
        claim.embedding_input_hash = text_hash(text)
    session.add(claim)
    await session.flush()
    evidence = Evidence(
        hypothesis_id=setup[1].id,
        claim_id=claim.id,
        stance="supports",
        independence_score=0.8,
        evidence_weight=0.7,
        source_quality_score=0.6,
        duplicate_group_id=group.id if group else None,
    )
    session.add(evidence)
    await session.flush()
    return evidence, claim, document


async def test_confirmed_copies_group_idempotently_preserve_records_and_clear_stale_scores(
    session, setup
):
    a, first, document = await report(session, setup)
    b, second, _ = await report(session, setup, document=document)
    result = await detect_duplicates(session, setup[1].id)
    assert result.complete and result.changed == 2 and len(result.groups) == 1
    assert a.duplicate_group_id == b.duplicate_group_id == result.groups[0]
    assert a.evidence_weight is None and b.independence_score is None
    assert a.source_quality_score == 0.6 and a.claim_id == first.id and b.claim_id == second.id
    assert first.structured_data["publication_status"] == "held"
    assert setup[1].confidence_score is None
    repeated = await detect_duplicates(session, setup[1].id)
    assert (
        repeated.run_id == result.run_id
        and repeated.groups == result.groups
        and repeated.changed == 0
    )
    assert (
        await session.scalar(
            select(func.count())
            .select_from(DuplicateAssessment)
            .where(DuplicateAssessment.run_id == result.run_id)
        )
        == 1
    )


async def test_similar_independent_accounts_are_not_grouped(session, setup):
    a, _, _ = await report(session, setup, quote="My own short fare report", vector=True)
    b, _, _ = await report(session, setup, quote="Another independent fare report", vector=True)
    result = await detect_duplicates(session, setup[1].id)
    assert result.groups == [] and result.suspected == 1 and result.changed == 0
    assert a.duplicate_group_id is None and b.duplicate_group_id is None
    assert a.independence_score == 0.8


async def test_mirrored_document_hash_confirms_same_report_across_sources(session, setup):
    a, _, _ = await report(session, setup, raw_hash="a" * 64)
    b, _, _ = await report(session, setup, raw_hash="a" * 64)
    result = await detect_duplicates(session, setup[1].id)
    assert len(result.groups) == 1 and a.duplicate_group_id == b.duplicate_group_id


async def test_same_page_distinct_claims_are_not_grouped(session, setup):
    _, _, document = await report(session, setup, quote="A meter refusal report")
    await report(
        session, setup, document=document, text="An ATM card was skimmed.", quote="An ATM report"
    )
    result = await detect_duplicates(session, setup[1].id)
    assert result.groups == [] and result.changed == 0


async def test_generic_shared_advisory_quote_and_unknown_authors_only_flag_review(session, setup):
    a, _, _ = await report(session, setup, author="anonymous")
    b, _, _ = await report(session, setup, author="anonymous")
    result = await detect_duplicates(session, setup[1].id)
    assert result.groups == [] and result.suspected == 1
    assert a.duplicate_group_id is None and b.duplicate_group_id is None


async def test_same_author_crosspost_of_long_account_groups(session, setup):
    a, _, _ = await report(session, setup, author="u123")
    b, _, _ = await report(session, setup, author="u123")
    result = await detect_duplicates(session, setup[1].id)
    assert len(result.groups) == 1 and a.duplicate_group_id == b.duplicate_group_id


async def test_manual_groups_are_preserved_and_reported_as_incomplete(session, setup):
    manual = DuplicateGroup(description="Operator assigned")
    session.add(manual)
    await session.flush()
    a, _, document = await report(session, setup, group=manual)
    b, _, _ = await report(session, setup, document=document)
    result = await detect_duplicates(session, setup[1].id)
    assert not result.complete and "manual_group_preserved" in result.review_reasons
    assert a.duplicate_group_id == manual.id and b.duplicate_group_id is None


async def test_recalculation_clears_outdated_auto_tags_but_keeps_historical_run(session, setup):
    a, _, document = await report(session, setup)
    b, claim, _ = await report(session, setup, document=document)
    original = await detect_duplicates(session, setup[1].id)
    claim.structured_data = {"evidence_quote": "A revised, different account"}
    await session.flush()
    revised = await detect_duplicates(session, setup[1].id)
    assert revised.run_id != original.run_id and revised.changed == 2 and revised.groups == []
    assert a.duplicate_group_id is None and b.duplicate_group_id is None
    assert await session.get(DuplicateDetectionRun, original.run_id)
    assert await session.get(DuplicateGroup, original.groups[0])


async def test_archived_evidence_tags_are_not_rewritten_after_split(session, setup):
    a, _, document = await report(session, setup)
    b, _, _ = await report(session, setup, document=document)
    original = await detect_duplicates(session, setup[1].id)
    await split_hypothesis(
        session,
        setup[1].id,
        [a.id],
        key=str(uuid4()),
        review=Review(actor="fixture", reason="Reviewed split"),
    )
    revised = await detect_duplicates(session, setup[1].id)
    assert revised.evidence == 1 and b.duplicate_group_id is None
    assert a.archived_at is not None and a.duplicate_group_id == original.groups[0]


async def test_stale_embeddings_are_not_used_as_similarity_signal(session, setup):
    await report(session, setup, quote="First report", vector=True)
    _, claim, _ = await report(
        session, setup, text="A different statement", quote="Other report", vector=True
    )
    claim.embedding_input_hash = "b" * 64
    await session.flush()
    result = await detect_duplicates(session, setup[1].id)
    assessment = await session.scalar(
        select(DuplicateAssessment).where(DuplicateAssessment.run_id == result.run_id)
    )
    assert assessment.signals["embedding_cosine"] is None and result.groups == []


async def test_unverified_clean_hash_cannot_establish_mirror(session, setup):
    _, _, first = await report(session, setup)
    _, _, second = await report(session, setup)
    second.clean_text_hash = first.clean_text_hash
    await session.flush()
    result = await detect_duplicates(session, setup[1].id)
    assert result.groups == []
    assessment = await session.scalar(
        select(DuplicateAssessment).where(DuplicateAssessment.run_id == result.run_id)
    )
    assert not assessment.signals["same_clean_hash"]


async def test_conflicting_incident_dates_require_review_even_for_exact_copies(session, setup):
    _, first, document = await report(session, setup)
    _, second, _ = await report(session, setup, document=document)
    first.occurred_at = datetime(2026, 1, 1, tzinfo=UTC)
    second.occurred_at = datetime(2026, 2, 1, tzinfo=UTC)
    await session.flush()
    result = await detect_duplicates(session, setup[1].id)
    assert result.groups == [] and result.suspected == 1


async def test_input_limit_failure_does_not_write_a_partial_run(session, setup):
    await report(session, setup)
    await report(session, setup)
    hypothesis_id = setup[1].id
    with pytest.raises(ValueError, match="limit_exceeded"):
        await detect_duplicates(session, hypothesis_id, limit=1)
    assert (
        await session.scalar(
            select(func.count())
            .select_from(DuplicateDetectionRun)
            .where(DuplicateDetectionRun.hypothesis_id == hypothesis_id)
        )
        == 0
    )


async def test_empty_current_evidence_creates_repeatable_empty_audit(session, setup):
    result = await detect_duplicates(session, setup[1].id)
    assert result.evidence == result.assessments == result.changed == 0 and result.complete
    assert (await detect_duplicates(session, setup[1].id)).run_id == result.run_id


async def test_short_quote_requires_matching_versioned_offsets(session, setup):
    _, first, document = await report(session, setup, quote="Meter refused")
    _, second, _ = await report(session, setup, document=document, quote="Meter refused")
    assert (await detect_duplicates(session, setup[1].id)).groups == []
    start = document.clean_text.index("Meter refused")
    for claim in (first, second):
        claim.structured_data = {
            **claim.structured_data,
            "extraction": {
                "clean_text_hash": document.clean_text_hash,
                "quote_start": start,
                "quote_end": start + len("Meter refused"),
            },
        }
    await session.flush()
    assert len((await detect_duplicates(session, setup[1].id)).groups) == 1
