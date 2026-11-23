import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.db.embeddings import search_candidates
from app.db.hypothesis_lifecycle import (
    LIFECYCLE_VERSION,
    Draft,
    Review,
    apply_resolution,
    canonical_hypothesis,
    get_relationships,
    link_hypotheses,
    merge_hypotheses,
    split_hypothesis,
    update_hypothesis,
)
from app.db.hypothesis_resolution import resolve_hypothesis
from app.embeddings import text_hash
from app.hypothesis_resolution import Decision
from app.integrations.sarvam import Completion
from app.models import (
    Claim,
    Document,
    Evidence,
    Hypothesis,
    HypothesisLifecycleEvent,
    HypothesisScoreSnapshot,
    Location,
    Source,
)
from app.models.evidence import EvidenceStance
from app.models.hypothesis import HypothesisStatus
from app.models.hypothesis_relation import HypothesisRelationType as Kind
from app.models.location import LocationType
from app.models.source import SourceType

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]
REVIEW = Review(actor="fixture-reviewer", reason="Inspected claims, geography and evidence.")


@pytest.fixture
async def setup(session):
    city = Location(name="Fixture city", canonical_name=str(uuid4()), type=LocationType.CITY)
    source = Source(platform="lifecycle-fixture", source_type=SourceType.USER_REPORT)
    session.add_all([city, source])
    await session.flush()
    document = Document(source_id=source.id, raw_object_path="raw/lifecycle", content_hash="e" * 64)
    session.add(document)
    await session.flush()
    embedder, client = AsyncMock(), AsyncMock()
    embedder.model, embedder.revision = "fixture", "lifecycle-v1"
    embedder.embed.return_value = [[1.0] + [0.0] * 383]
    return city, document, embedder, client


async def claim(
    session, setup, text="The taxi driver refused the meter and inflated the fare.", **extra
):
    city, document, _, _ = setup
    row = Claim(
        document_id=document.id,
        location_id=city.id,
        claim_text=text,
        normalized_claim=text.casefold(),
        claim_type="transport",
        **extra,
    )
    session.add(row)
    await session.flush()
    return row


async def hypothesis(
    session, setup, description="Taxi drivers refuse meters and inflate fares.", **extra
):
    city, _, embedder, _ = setup
    title = "Reported taxi fare inflation"
    row = Hypothesis(
        primary_location_id=city.id,
        title=title,
        canonical_description=description,
        category="transport",
        embedding=[1.0] + [0.0] * 383,
        embedding_model=embedder.model,
        embedding_revision=embedder.revision,
        embedding_input_hash=text_hash(title + "\n" + description),
        **extra,
    )
    session.add(row)
    await session.flush()
    return row


async def evidence(session, target, item, stance=EvidenceStance.SUPPORTS, **extra):
    row = Evidence(hypothesis_id=target.id, claim_id=item.id, stance=stance, **extra)
    session.add(row)
    await session.flush()
    return row


async def resolution(session, setup, item, decision="NEW", target=None):
    _, _, embedder, client = setup
    if target is not None:
        client.complete.return_value = Completion(
            data=Decision(
                decision=decision,
                target_id=str(target.id) if decision != "NEW" else None,
                confidence=0.95,
                ambiguous=False,
                reason="Fixture reviewed comparison",
            ),
            requests=1,
        )
    return await resolve_hypothesis(session, item.id, embedder=embedder, client=client)


async def test_new_candidate_defaults_dates_and_idempotent_application(session, setup):
    observed = datetime(2026, 10, 1, tzinfo=UTC)
    item = await claim(
        session, setup, created_at=observed, occurred_at=datetime(2020, 1, 1, tzinfo=UTC)
    )
    result = await resolution(session, setup, item)
    event = await apply_resolution(session, result.id, embedder=setup[2])
    row = await session.get(Hypothesis, event.target_id)
    assert row.status == HypothesisStatus.CANDIDATE
    assert row.canonical_description == item.claim_text and row.category == "transport"
    assert row.first_seen == row.last_seen == observed
    assert row.confidence_score is None and row.embedding is None
    assert (await apply_resolution(session, result.id, embedder=setup[2])).id == event.id
    attached = await session.scalar(select(Evidence).where(Evidence.hypothesis_id == row.id))
    assert attached.claim_id == item.id and attached.archived_at is None
    assert event.details["claim_id"] == str(item.id)
    assert event.details["version"] == LIFECYCLE_VERSION


async def test_semantic_decisions_need_review_and_preserve_publication_hold(session, setup):
    item = await claim(
        session, setup, structured_data={"publication_status": "held", "sensitive": True}
    )
    target = await hypothesis(session, setup)
    result = await resolution(session, setup, item, "SAME", target)
    with pytest.raises(ValueError, match="requires_review"):
        await apply_resolution(session, result.id, embedder=setup[2])
    event = await apply_resolution(session, result.id, embedder=setup[2], review=REVIEW)
    assert event.target_id == target.id and event.actor == REVIEW.actor
    await session.refresh(item)
    assert item.structured_data["publication_status"] == "held"


@pytest.mark.parametrize(
    "decision,kind", [("RELATED", Kind.RELATED_TO), ("SUBTYPE", Kind.SUBTYPE_OF)]
)
async def test_distinct_patterns_create_separate_candidate_and_directed_relationship(
    session, setup, decision, kind
):
    item = await claim(session, setup)
    target = await hypothesis(session, setup)
    result = await resolution(session, setup, item, decision, target)
    event = await apply_resolution(session, result.id, embedder=setup[2], review=REVIEW)
    assert event.target_id != target.id
    relations = await get_relationships(session, event.target_id)
    assert len(relations) == 1 and relations[0].relation_type == kind
    if kind == Kind.SUBTYPE_OF:
        assert relations[0].source_hypothesis_id == event.target_id
        assert relations[0].target_hypothesis_id == target.id


async def test_contradiction_does_not_extend_supporting_observation_dates(session, setup):
    prior = await claim(
        session, setup, "Earlier supporting report", created_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    negative = await claim(
        session,
        setup,
        "The specific allegation was retracted.",
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    target = await hypothesis(session, setup)
    await evidence(session, target, prior)
    result = await resolution(session, setup, negative, "CONTRADICTS", target)
    event = await apply_resolution(session, result.id, embedder=setup[2], review=REVIEW)
    attached = await session.get(Evidence, event.details["evidence_id"])
    assert attached.stance == EvidenceStance.CONTRADICTS
    assert target.first_seen == target.last_seen == prior.created_at


async def test_merge_preserves_original_evidence_ids_scores_history_and_relationships(
    session, setup
):
    first = await claim(
        session, setup, "First fare report", created_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    last = await claim(
        session, setup, "Last fare report", created_at=datetime(2026, 2, 1, tzinfo=UTC)
    )
    source, target, neighbor = [await hypothesis(session, setup) for _ in range(3)]
    original = await evidence(
        session,
        source,
        first,
        source_quality_score=0.7,
        independence_score=0.6,
        evidence_weight=0.4,
    )
    await evidence(session, target, last)
    snapshot = HypothesisScoreSnapshot(
        hypothesis_id=source.id,
        confidence_score=0.8,
        prevalence_score=0.2,
        recency_score=0.6,
        scoring_version="fixture",
        explanation="Historical score",
    )
    session.add(snapshot)
    await link_hypotheses(
        session, source.id, neighbor.id, Kind.RELATED_TO, key=str(uuid4()), review=REVIEW
    )
    event = await merge_hypotheses(
        session, source.id, target.id, key="merge-fixture", review=REVIEW
    )
    await session.refresh(original)
    replacement = await session.get(Evidence, original.superseded_by_id)
    assert original.archived_at and original.claim_id == first.id
    assert replacement.hypothesis_id == target.id and replacement.source_quality_score == 0.7
    assert replacement.independence_score == 0.6 and replacement.evidence_weight == 0.4
    assert source.status == HypothesisStatus.INACTIVE and source.merged_into_id == target.id
    assert target.first_seen == first.created_at and target.last_seen == last.created_at
    assert (await canonical_hypothesis(session, source.id)).id == target.id
    assert any(
        edge.relation_type == Kind.RELATED_TO
        for edge in await get_relationships(session, target.id)
    )
    assert await session.get(HypothesisScoreSnapshot, snapshot.id) is not None
    assert (
        await merge_hypotheses(session, source.id, target.id, key="merge-fixture", review=REVIEW)
    ).id == event.id


async def test_merge_deduplicates_same_claim_without_overwriting_target_scores(session, setup):
    item = await claim(session, setup)
    source, target = [await hypothesis(session, setup) for _ in range(2)]
    original = await evidence(session, source, item, evidence_weight=0.4)
    existing = await evidence(session, target, item, evidence_weight=0.8)
    await merge_hypotheses(session, source.id, target.id, key=str(uuid4()), review=REVIEW)
    assert original.superseded_by_id == existing.id and existing.evidence_weight == 0.8
    assert (
        await session.scalar(
            select(func.count())
            .select_from(Evidence)
            .where(Evidence.hypothesis_id == target.id, Evidence.archived_at.is_(None))
        )
        == 1
    )


async def test_conflicting_stances_roll_back_entire_merge_and_reservation(session, setup):
    item = await claim(session, setup)
    source, target = [await hypothesis(session, setup) for _ in range(2)]
    await evidence(session, source, item)
    await evidence(session, target, item, EvidenceStance.CONTRADICTS)
    source_id = source.id
    with pytest.raises(ValueError, match="attachment_conflict"):
        await merge_hypotheses(
            session, source.id, target.id, key="conflicting-merge", review=REVIEW
        )
    await session.refresh(source)
    assert source.merged_into_id is None and source.status == HypothesisStatus.CANDIDATE
    assert (
        await session.scalar(
            select(HypothesisLifecycleEvent.id).where(
                HypothesisLifecycleEvent.key == "conflicting-merge"
            )
        )
        is None
    )
    assert (
        await session.scalar(
            select(func.count())
            .select_from(Evidence)
            .where(Evidence.hypothesis_id == source_id, Evidence.archived_at.is_(None))
        )
        == 1
    )


async def test_split_archives_selected_attachments_and_leaves_remaining_evidence_active(
    session, setup
):
    source = await hypothesis(session, setup)
    one, two = [
        await claim(session, setup, text)
        for text in ("First pattern report", "Other pattern report")
    ]
    left, selected = [await evidence(session, source, item) for item in (one, two)]
    event = await split_hypothesis(
        session,
        source.id,
        [selected.id],
        key="split-fixture",
        review=REVIEW,
        draft=Draft(title="Other pattern", description=two.claim_text, category="transport"),
        relation_type=Kind.SUBTYPE_OF,
    )
    child = await session.get(Hypothesis, event.target_id)
    assert child.status == HypothesisStatus.CANDIDATE and child.title == "Other pattern"
    assert selected.archived_at and left.archived_at is None
    assert (await session.get(Evidence, selected.superseded_by_id)).hypothesis_id == child.id
    assert source.status == HypothesisStatus.CANDIDATE and source.first_seen == one.created_at
    assert (
        await split_hypothesis(
            session,
            source.id,
            [selected.id],
            key="split-fixture",
            review=REVIEW,
            draft=Draft(title="Other pattern", description=two.claim_text, category="transport"),
            relation_type=Kind.SUBTYPE_OF,
        )
    ).id == event.id


async def test_splitting_all_support_retires_parent_without_false_redirect(session, setup):
    source = await hypothesis(session, setup)
    selected = await evidence(session, source, await claim(session, setup))
    event = await split_hypothesis(
        session, source.id, [selected.id], key=str(uuid4()), review=REVIEW
    )
    assert source.status == HypothesisStatus.INACTIVE and source.merged_into_id is None
    assert event.target_id != source.id and source.first_seen is None


async def test_update_invalidates_vectors_and_scores_but_keeps_score_snapshots(session, setup):
    row = await hypothesis(session, setup, confidence_score=0.9)
    event = await update_hypothesis(
        session,
        row.id,
        Draft(title="Reviewed title", description="Reviewed description", category="reviewed"),
        key="edit-fixture",
        review=REVIEW,
    )
    assert (
        row.embedding is None and row.embedding_input_hash is None and row.confidence_score is None
    )
    assert event.details["before"]["confidence_score"] == 0.9
    assert event.details["after"]["title"] == "Reviewed title"


async def test_relationship_symmetry_direction_and_subtype_cycles(session, setup):
    a, b, c = [await hypothesis(session, setup) for _ in range(3)]
    first = await link_hypotheses(
        session, a.id, b.id, Kind.RELATED_TO, key=str(uuid4()), review=REVIEW
    )
    reverse = await link_hypotheses(
        session, b.id, a.id, Kind.RELATED_TO, key=str(uuid4()), review=REVIEW
    )
    assert first.details["relation_id"] == reverse.details["relation_id"]
    await link_hypotheses(session, a.id, b.id, Kind.SUBTYPE_OF, key=str(uuid4()), review=REVIEW)
    await link_hypotheses(session, b.id, c.id, Kind.SUBTYPE_OF, key=str(uuid4()), review=REVIEW)
    with pytest.raises(ValueError, match="subtype_cycle"):
        await link_hypotheses(session, c.id, a.id, Kind.SUBTYPE_OF, key=str(uuid4()), review=REVIEW)


async def test_idempotency_key_cannot_be_reused_for_different_intent(session, setup):
    a, b, c = [await hypothesis(session, setup) for _ in range(3)]
    await link_hypotheses(session, a.id, b.id, Kind.RELATED_TO, key="same-key", review=REVIEW)
    with pytest.raises(ValueError, match="key_conflict"):
        await link_hypotheses(session, a.id, c.id, Kind.RELATED_TO, key="same-key", review=REVIEW)


async def test_retired_hypotheses_cannot_be_retrieved_or_exact_matched(session, setup):
    item = await claim(session, setup)
    source = await hypothesis(session, setup, item.claim_text)
    target = await hypothesis(session, setup)
    source_id, target_id = source.id, target.id
    await merge_hypotheses(session, source_id, target_id, key=str(uuid4()), review=REVIEW)
    candidates = await search_candidates(session, item.claim_text, setup[0].id, setup[2])
    assert [entry.hypothesis_id for entry in candidates] == [target_id]
    result = await resolution(session, setup, item, "RELATED", target)
    assert result.details["context"]["method"] == "semantic"
    assert result.details["context"]["coverage"]["total"] == 1


async def test_stale_resolution_cannot_be_applied_even_with_review(session, setup):
    item = await claim(session, setup)
    target = await hypothesis(session, setup)
    result = await resolution(session, setup, item, "SAME", target)
    result_id = result.id
    item.claim_text = "The report was retracted."
    await session.flush()
    with pytest.raises(ValueError, match="inputs_changed"):
        await apply_resolution(session, result_id, embedder=setup[2], review=REVIEW)


async def test_same_attachment_updates_last_seen_from_database_observation_time(session, setup):
    first = await claim(
        session, setup, "Earlier report", created_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    target = await hypothesis(session, setup)
    await evidence(session, target, first)
    latest = await claim(
        session, setup, target.canonical_description, created_at=datetime(2026, 5, 1, tzinfo=UTC)
    )
    result = await resolution(session, setup, latest)
    assert result.decision == "SAME" and not result.needs_review
    await apply_resolution(session, result.id, embedder=setup[2])
    assert target.first_seen == first.created_at and target.last_seen == latest.created_at


async def test_cross_location_merge_is_rejected(session, setup):
    a, b = [await hypothesis(session, setup) for _ in range(2)]
    foreign = Location(name="Foreign", canonical_name=str(uuid4()), type=LocationType.CITY)
    session.add(foreign)
    await session.flush()
    b.primary_location_id = foreign.id
    await session.flush()
    with pytest.raises(ValueError, match="same_location"):
        await merge_hypotheses(session, a.id, b.id, key=str(uuid4()), review=REVIEW)


async def test_split_cannot_take_another_hypothesis_evidence(session, setup):
    a, b = [await hypothesis(session, setup) for _ in range(2)]
    other = await evidence(session, b, await claim(session, setup))
    with pytest.raises(ValueError, match="not_owned"):
        await split_hypothesis(session, a.id, [other.id], key=str(uuid4()), review=REVIEW)


async def test_split_cannot_create_positive_candidate_from_only_contradiction(session, setup):
    row = await hypothesis(session, setup)
    negative = await evidence(session, row, await claim(session, setup), EvidenceStance.CONTRADICTS)
    with pytest.raises(ValueError, match="supporting_evidence"):
        await split_hypothesis(session, row.id, [negative.id], key=str(uuid4()), review=REVIEW)


async def test_manual_mutation_requires_review_identity_and_reason(session, setup):
    a, b = [await hypothesis(session, setup) for _ in range(2)]
    with pytest.raises(ValueError, match="review_identity"):
        await merge_hypotheses(session, a.id, b.id, key=str(uuid4()), review=None)


async def test_canonical_lookup_defends_against_legacy_merge_cycles(session, setup):
    a, b = [await hypothesis(session, setup) for _ in range(2)]
    a.merged_into_id, b.merged_into_id = b.id, a.id
    a.status = b.status = HypothesisStatus.INACTIVE
    await session.flush()
    with pytest.raises(ValueError, match="cycle"):
        await canonical_hypothesis(session, a.id)


async def test_relationship_query_paginates_original_edges(session, setup):
    a, b, c = [await hypothesis(session, setup) for _ in range(3)]
    await link_hypotheses(session, a.id, b.id, Kind.RELATED_TO, key=str(uuid4()), review=REVIEW)
    await link_hypotheses(session, a.id, c.id, Kind.SUBTYPE_OF, key=str(uuid4()), review=REVIEW)
    page = await get_relationships(session, a.id, limit=1)
    next_page = await get_relationships(session, a.id, limit=1, after=page[0].id)
    assert len(next_page) == 1 and next_page[0].id > page[0].id


async def test_new_resolution_becomes_stale_when_scope_changes(session, setup):
    item = await claim(session, setup)
    result = await resolution(session, setup, item)
    await hypothesis(session, setup)
    with pytest.raises(ValueError, match="inputs_changed"):
        await apply_resolution(session, result.id, embedder=setup[2])


async def test_outdated_resolver_version_requires_a_fresh_decision(session, setup):
    item = await claim(session, setup)
    result = await resolution(session, setup, item)
    result.version = "hypothesis-v2"
    await session.flush()
    with pytest.raises(ValueError, match="version_changed"):
        await apply_resolution(session, result.id, embedder=setup[2], review=REVIEW)


async def test_merge_chains_keep_evidence_and_alias_provenance(session, setup):
    a, b, c = [await hypothesis(session, setup) for _ in range(3)]
    original = await evidence(session, a, await claim(session, setup))
    original_id, a_id, b_id, c_id = original.id, a.id, b.id, c.id
    await merge_hypotheses(session, a_id, b_id, key=str(uuid4()), review=REVIEW)
    await merge_hypotheses(session, b_id, c_id, key=str(uuid4()), review=REVIEW)
    assert (await canonical_hypothesis(session, a_id)).id == c_id
    original = await session.get(Evidence, original_id)
    intermediate = await session.get(Evidence, original.superseded_by_id)
    current = await session.get(Evidence, intermediate.superseded_by_id)
    assert current.hypothesis_id == c_id and current.archived_at is None
    assert original.archived_at and intermediate.archived_at
    assert len(await get_relationships(session, c_id)) == 2
