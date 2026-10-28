import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.confidence import calculate_confidence
from app.db.evidence_weight import calculate_evidence_weights
from app.db.hypothesis_lifecycle import Review, split_hypothesis
from app.db.independence import calculate_independence
from app.embeddings import text_hash
from app.models import (
    Claim,
    Document,
    Evidence,
    EvidenceWeightRun,
    Hypothesis,
    HypothesisScoreSnapshot,
    Location,
    Source,
    UserReport,
)
from app.models.location import LocationType
from app.models.source import SourceType
from app.models.user_report import ReportProvenance

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]


@pytest.fixture
async def setup(session):
    location = Location(name="Quality fixture", canonical_name=str(uuid4()), type=LocationType.CITY)
    session.add(location)
    await session.flush()
    hypothesis = Hypothesis(
        primary_location_id=location.id,
        title="Reported fares",
        canonical_description="Reports of fare inflation",
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
    source_type=SourceType.FORUM,
    provenance=None,
    status="processed",
    metadata=None,
    data=None,
    stance="supports",
    raw_hash=None,
    quote=None,
):
    source = Source(
        platform="forum",
        source_type=source_type,
        canonical_url=f"https://example.org/{uuid4()}",
        source_metadata=metadata or {},
    )
    session.add(source)
    await session.flush()
    document = Document(
        source_id=source.id,
        raw_object_path=f"raw/{uuid4()}",
        content_hash=raw_hash or text_hash(str(uuid4())),
    )
    session.add(document)
    await session.flush()
    normalized = None
    if provenance is not None:
        normalized = UserReport(
            document_id=document.id,
            report_text="A submitted fare report",
            provenance_class=provenance,
            moderation_status=status,
        )
        session.add(normalized)
    claim = Claim(
        document_id=document.id,
        location_id=setup[0].id,
        claim_type="transport",
        claim_text="A driver refused the meter and inflated the fare.",
        normalized_claim="meter refusal",
        structured_data={
            "first_hand": True,
            "sensitive": False,
            "publication_status": "unreviewed",
            "evidence_quote": quote or f"Distinct account {uuid4()}",
            **(data or {}),
        },
    )
    session.add(claim)
    await session.flush()
    evidence = Evidence(hypothesis_id=setup[1].id, claim_id=claim.id, stance=stance)
    session.add(evidence)
    await session.flush()
    return evidence, claim, source, normalized


async def test_quality_weight_components_and_unchanged_run_reuse(session, setup):
    evidence, _, _, _ = await report(session, setup)
    result = await calculate_evidence_weights(session, setup[1].id)
    assert result.complete and result.eligible == 1 and result.total_weight == 0.325
    assert evidence.source_quality_score == 0.65 and evidence.evidence_weight == 0.325
    assert evidence.weight_components["independence_score"] == 0.5
    repeated = await calculate_evidence_weights(session, setup[1].id)
    assert repeated.run_id == result.run_id and repeated.changed == 0
    assert (
        await session.scalar(
            select(func.count())
            .select_from(EvidenceWeightRun)
            .where(EvidenceWeightRun.hypothesis_id == setup[1].id)
        )
        == 1
    )


async def test_confidence_snapshots_reuse_current_inputs_and_keep_unknown_metrics(session, setup):
    evidence, _, _, _ = await report(session, setup)
    evidence.semantic_match_score = 1
    await session.flush()
    first = await calculate_confidence(session, setup[1].id)
    assert first.score == round(0.8 * 0.325 / 2.325, 6)
    assert first.label == "limited" and first.created
    assert setup[1].confidence_score == first.score
    assert setup[1].status.value == "candidate"
    snapshot = await session.get(HypothesisScoreSnapshot, first.snapshot_id)
    assert snapshot.recency_score is snapshot.prevalence_score is None
    setup[1].prevalence_score, setup[1].recency_score = 0.4, 0.6
    await session.flush()
    repeated = await calculate_confidence(session, setup[1].id)
    assert repeated.snapshot_id == first.snapshot_id and not repeated.created
    assert (setup[1].prevalence_score, setup[1].recency_score) == (0.4, 0.6)
    evidence.semantic_match_score = 0.5
    await session.flush()
    changed = await calculate_confidence(session, setup[1].id)
    assert changed.score < first.score and changed.weight_run_id == first.weight_run_id
    assert changed.snapshot_id != first.snapshot_id
    assert snapshot.confidence_score == first.score
    assert snapshot.components["contributions"][0]["semantic_factor"] == 1


async def test_confidence_holds_copies_and_contradictions_end_to_end(session, setup):
    quote = (
        "A driver at the station refused the meter and demanded a much higher fixed fare "
        "before taking the traveler to a hotel late that evening."
    )
    support, _, _, _ = await report(session, setup, raw_hash="a" * 64, quote=quote)
    support.semantic_match_score = 1
    await session.flush()
    first = await calculate_confidence(session, setup[1].id)
    copy, _, _, _ = await report(session, setup, raw_hash="a" * 64, quote=quote)
    copy.semantic_match_score = 1
    await session.flush()
    copies = await calculate_confidence(session, setup[1].id)
    assert copies.score == first.score
    counter, _, _, _ = await report(session, setup, stance="contradicts")
    counter.semantic_match_score = 1
    await session.flush()
    contradicted = await calculate_confidence(session, setup[1].id)
    assert 0 < contradicted.score < first.score
    held, _, _, _ = await report(session, setup, data={"sensitive": True})
    held.semantic_match_score = 1
    await session.flush()
    result = await calculate_confidence(session, setup[1].id)
    assert result.score == contradicted.score
    snapshot = await session.get(HypothesisScoreSnapshot, result.snapshot_id)
    assert (
        next(c for c in snapshot.components["contributions"] if c["evidence_id"] == str(held.id))[
            "effective_weight"
        ]
        == 0
    )


async def test_confidence_location_edit_is_hashed_and_other_location_excluded(session, setup):
    evidence, claim, _, _ = await report(session, setup)
    evidence.semantic_match_score = 1
    await session.flush()
    exact = await calculate_confidence(session, setup[1].id)
    claim.location_id = None
    await session.flush()
    unknown = await calculate_confidence(session, setup[1].id)
    assert unknown.score < exact.score
    assert "location_agreement_unknown" in unknown.review_reasons
    other = Location(name="Elsewhere", canonical_name=str(uuid4()), type=LocationType.CITY)
    session.add(other)
    await session.flush()
    claim.location_id = other.id
    await session.flush()
    different = await calculate_confidence(session, setup[1].id)
    assert different.score == 0 and different.label == "no_support"
    assert len({exact.snapshot_id, unknown.snapshot_id, different.snapshot_id}) == 3


async def test_confidence_empty_candidate_is_queryable_and_does_not_invent_metrics(session, setup):
    result = await calculate_confidence(session, setup[1].id)
    assert result.score == 0 and result.label == "no_support"
    assert setup[1].status.value == "candidate"
    assert setup[1].prevalence_score is setup[1].recency_score is None


async def test_confidence_preview_rollback_restores_history_and_cache(session, setup):
    await report(session, setup)
    async with session.begin_nested() as preview:
        result = await calculate_confidence(session, setup[1].id)
        assert result.score > 0
        await preview.rollback()
    await session.refresh(setup[1])
    assert setup[1].confidence_score == 0.8
    assert await session.get(HypothesisScoreSnapshot, result.snapshot_id) is None


async def test_confidence_respects_upstream_incomplete_provenance_and_bound(session, setup):
    await report(session, setup, raw_hash="b" * 64, metadata={"reference_urls": ["invalid"]})
    await report(session, setup, raw_hash="b" * 64)
    result = await calculate_confidence(session, setup[1].id)
    assert not result.complete and result.review_reasons
    assert "incomplete" in result.explanation
    with pytest.raises(ValueError, match="duplicate_evidence_limit_exceeded"):
        await calculate_confidence(session, setup[1].id, limit=1)
    assert setup[1].confidence_score == result.score


async def test_confidence_rejects_inactive_hypotheses_and_excludes_archived_evidence(
    session, setup
):
    evidence, _, _, _ = await report(session, setup)
    retained, _, _, _ = await report(session, setup)
    evidence.semantic_match_score = 1
    retained.semantic_match_score = 1
    await session.flush()
    before = await calculate_confidence(session, setup[1].id)
    event = await split_hypothesis(
        session,
        setup[1].id,
        [evidence.id],
        key=str(uuid4()),
        review=Review(actor="fixture", reason="scoring history"),
    )
    after = await calculate_confidence(session, setup[1].id)
    assert 0 < after.score < before.score
    current = await session.get(HypothesisScoreSnapshot, after.snapshot_id)
    assert [c["evidence_id"] for c in current.components["contributions"]] == [str(retained.id)]
    assert (
        await session.get(HypothesisScoreSnapshot, before.snapshot_id)
    ).confidence_score == before.score
    assert event.target_id is not None
    setup[1].status = "inactive"
    await session.flush()
    with pytest.raises(ValueError, match="hypothesis_not_current"):
        await calculate_confidence(session, setup[1].id)


@pytest.mark.parametrize(
    "provenance,quality",
    [
        (ReportProvenance.FIRST_HAND, 0.65),
        (ReportProvenance.WITNESSED, 0.6),
        (ReportProvenance.SECOND_HAND, 0.4),
    ],
)
async def test_normalized_report_provenance_overrides_extraction_classification(
    session, setup, provenance, quality
):
    evidence, _, _, _ = await report(
        session,
        setup,
        source_type=SourceType.USER_REPORT,
        provenance=provenance,
        data={"first_hand": False},
    )
    result = await calculate_evidence_weights(session, setup[1].id)
    assert result.eligible == 1 and evidence.source_quality_score == quality
    assert evidence.weight_components["provenance"] == provenance.value


async def test_moderation_recalculation_retains_old_run_and_does_not_clear_holds(session, setup):
    evidence, claim, _, submitted = await report(
        session,
        setup,
        source_type=SourceType.USER_REPORT,
        provenance=ReportProvenance.FIRST_HAND,
        status="pending",
    )
    before = await calculate_evidence_weights(session, setup[1].id)
    assert before.excluded == 1 and evidence.evidence_weight == 0
    submitted.moderation_status = "processed"
    await session.flush()
    after = await calculate_evidence_weights(session, setup[1].id)
    assert after.run_id != before.run_id and after.independence_run_id == before.independence_run_id
    assert evidence.evidence_weight == 0.325
    assert (await session.get(EvidenceWeightRun, before.run_id)).scores[str(evidence.id)][
        "weight"
    ] == 0
    claim.structured_data = {
        **claim.structured_data,
        "publication_status": "held",
        "sensitive": True,
    }
    submitted.moderation_status = "accepted"
    await session.flush()
    held = await calculate_evidence_weights(session, setup[1].id)
    assert held.excluded == 1 and evidence.evidence_weight == 0
    assert claim.structured_data["publication_status"] == "held"


@pytest.mark.parametrize("status", ["rejected", "needs_review"])
async def test_rejected_and_review_pending_reports_are_excluded(session, setup, status):
    evidence, _, _, _ = await report(
        session,
        setup,
        source_type=SourceType.USER_REPORT,
        provenance=ReportProvenance.FIRST_HAND,
        status=status,
    )
    result = await calculate_evidence_weights(session, setup[1].id)
    assert result.excluded == 1 and result.total_weight == evidence.evidence_weight == 0


async def test_named_actor_is_excluded_even_if_sensitivity_flag_is_false(session, setup):
    evidence, _, _, _ = await report(
        session, setup, data={"entities": ["Fixture Business"], "publication_status": "approved"}
    )
    result = await calculate_evidence_weights(session, setup[1].id)
    assert result.excluded == 1 and evidence.evidence_weight == 0
    assert "named_actor_requires_review" in evidence.weight_components["exclusion_reasons"]


async def test_source_and_provenance_edits_create_new_quality_history_without_new_independence(
    session, setup
):
    evidence, claim, _, _ = await report(session, setup)
    first = await calculate_evidence_weights(session, setup[1].id)
    claim.structured_data = {**claim.structured_data, "first_hand": False}
    await session.flush()
    second = await calculate_evidence_weights(session, setup[1].id)
    assert second.run_id != first.run_id and second.independence_run_id == first.independence_run_id
    assert evidence.source_quality_score == 0.4 and evidence.evidence_weight == 0.2
    assert (await session.get(EvidenceWeightRun, first.run_id)).scores[str(evidence.id)][
        "source_quality"
    ] == 0.65


async def test_official_verification_bonus_uses_explicit_boolean_metadata(session, setup):
    evidence, _, source, _ = await report(
        session,
        setup,
        source_type=SourceType.OFFICIAL_ADVISORY,
        data={"first_hand": None},
        metadata={"official_advisory_verified": "true"},
    )
    await calculate_evidence_weights(session, setup[1].id)
    assert evidence.source_quality_score == 0.7
    source.source_metadata = {"official_advisory_verified": True}
    await session.flush()
    await calculate_evidence_weights(session, setup[1].id)
    assert evidence.source_quality_score == 0.8


async def test_normalized_report_never_receives_official_source_prior(session, setup):
    evidence, _, _, _ = await report(
        session,
        setup,
        source_type=SourceType.OFFICIAL_ADVISORY,
        provenance=ReportProvenance.FIRST_HAND,
        metadata={"official_advisory_verified": True},
    )
    await calculate_evidence_weights(session, setup[1].id)
    assert evidence.source_quality_score == 0.65
    assert evidence.weight_components["effective_source_type"] == "user_report"


async def test_fresh_independence_run_invalidates_weight_cache_but_preserves_history(
    session, setup
):
    evidence, _, source, _ = await report(session, setup)
    original = await calculate_evidence_weights(session, setup[1].id)
    source.published_at = datetime(2026, 1, 1, tzinfo=UTC)
    await session.flush()
    await calculate_independence(session, setup[1].id)
    assert evidence.evidence_weight is None and evidence.weight_run_id is None
    assert evidence.weight_components == {} and evidence.source_quality_score == 0.65
    assert (await session.get(EvidenceWeightRun, original.run_id)).scores[str(evidence.id)][
        "weight"
    ] == 0.325
    refreshed = await calculate_evidence_weights(session, setup[1].id)
    assert refreshed.run_id != original.run_id and evidence.source_quality_score == 0.7


async def test_lifecycle_transfer_preserves_archived_weight_components(session, setup):
    a, _, _, _ = await report(session, setup)
    b, _, _, _ = await report(session, setup)
    result = await calculate_evidence_weights(session, setup[1].id)
    components = a.weight_components
    event = await split_hypothesis(
        session,
        setup[1].id,
        [a.id],
        key=str(uuid4()),
        review=Review(actor="operator", reason="Reviewed split"),
    )
    replacement = await session.get(Evidence, a.superseded_by_id)
    assert a.archived_at and a.weight_run_id == result.run_id and a.weight_components == components
    assert replacement.weight_run_id is None and replacement.evidence_weight is None
    assert b.weight_run_id is None and b.weight_components == {}
    child = await calculate_evidence_weights(session, event.target_id)
    assert child.evidence == 1 and child.total_weight == 0.325


async def test_duplicate_copies_cannot_gain_weight_from_source_quality(session, setup):
    quote = (
        "At the station a driver refused the meter and demanded a much higher fixed fare "
        "before taking the traveler to the hotel late that evening."
    )
    a, _, _, _ = await report(session, setup, quote=quote, raw_hash="a" * 64)
    b, _, _, _ = await report(session, setup, quote=quote, raw_hash="a" * 64)
    result = await calculate_evidence_weights(session, setup[1].id)
    assert result.total_weight == 0.325 and sorted([a.evidence_weight, b.evidence_weight]) == [
        0,
        0.325,
    ]


async def test_uncertain_or_related_stance_does_not_corroborate(session, setup):
    evidence, _, _, _ = await report(session, setup, stance="related")
    result = await calculate_evidence_weights(session, setup[1].id)
    assert result.eligible == 0 and evidence.evidence_weight == 0


async def test_moderation_database_constraint_rejects_unknown_states(session, setup):
    _, _, _, submitted = await report(
        session, setup, source_type=SourceType.USER_REPORT, provenance=ReportProvenance.FIRST_HAND
    )
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            submitted.moderation_status = "trusted"
            await session.flush()


async def test_empty_cohort_has_repeatable_zero_weight_and_unknown_cached_confidence(
    session, setup
):
    result = await calculate_evidence_weights(session, setup[1].id)
    assert result.evidence == result.eligible == result.excluded == result.total_weight == 0
    assert setup[1].confidence_score is None
    assert (await calculate_evidence_weights(session, setup[1].id)).run_id == result.run_id
