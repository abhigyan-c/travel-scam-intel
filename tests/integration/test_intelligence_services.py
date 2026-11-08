import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.embeddings import text_hash
from app.models import (
    Claim,
    Document,
    Evidence,
    Hypothesis,
    HypothesisRelation,
    Location,
    LocationResearchState,
    ReportAttachment,
    ResearchJob,
    Source,
    UserReport,
)
from app.services.intelligence import (
    IntelligenceInputError,
    IntelligenceNotFound,
    IntelligenceService,
)
from tests.integration.test_on_demand_research import environment as environment

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]
NOW = datetime(2026, 10, 30, tzinfo=UTC)
SUMMARY = "A driver demanded extra money after an agreed fixed fare."


async def seed(
    ctx,
    location_id,
    *,
    hypothesis_id=None,
    summary=SUMMARY,
    data=None,
    occurred_at=NOW - timedelta(days=2),
    report=False,
    status="active",
):
    quote = "I agreed a fixed fare but the driver later demanded extra money."
    async with ctx["sessions"]() as session, session.begin():
        source = Source(
            platform="example.org",
            canonical_url=f"https://example.org/{uuid4()}",
            source_type="user_report" if report else "forum",
            author_identifier="private-author",
        )
        session.add(source)
        await session.flush()
        document = Document(
            source_id=source.id,
            raw_object_path=f"private/{uuid4()}",
            content_hash=text_hash(str(uuid4())),
            clean_text=quote,
            clean_text_hash=text_hash(quote),
            language="en",
            processing_status="processed",
            published_at=NOW,
        )
        session.add(document)
        await session.flush()
        metadata = {
            "sensitive": False,
            "entities": [],
            "publication_status": "unreviewed",
            "evidence_quote": quote,
            "first_hand": True,
            "location": "Fiji",
            "occurred_at": occurred_at.isoformat() if occurred_at else None,
            "extraction": {
                "clean_text_hash": text_hash(quote),
                "quote_start": 0,
                "quote_end": len(quote),
            },
            **(data or {}),
        }
        claim = Claim(
            document_id=document.id,
            location_id=location_id,
            claim_text=summary,
            normalized_claim=summary.lower(),
            claim_type="transport",
            occurred_at=occurred_at,
            structured_data=metadata,
            processing_status="processed",
        )
        session.add(claim)
        if hypothesis_id is None:
            hypothesis = Hypothesis(
                primary_location_id=location_id,
                title="PRIVATE INTERNAL TITLE",
                canonical_description="PRIVATE INTERNAL DESCRIPTION",
                category="internal",
                status=status,
                confidence_score=0.99,
                recency_score=0.99,
                prevalence_score=0.99,
            )
            session.add(hypothesis)
            await session.flush()
            hypothesis_id = hypothesis.id
        await session.flush()
        evidence = Evidence(
            hypothesis_id=hypothesis_id,
            claim_id=claim.id,
            stance="supports",
            semantic_match_score=1,
            independence_score=1,
            evidence_weight=1,
        )
        session.add(evidence)
        user_report = None
        if report:
            user_report = UserReport(
                document_id=document.id,
                location_id=location_id,
                report_text="PRIVATE REPORT NARRATIVE",
                provenance_class="SECOND_HAND",
                moderation_status="processed",
                occurred_at=occurred_at,
            )
            session.add(user_report)
        await session.flush()
        return (
            hypothesis_id,
            claim.id,
            evidence.id,
            source.id,
            document.id,
            user_report.id if report else None,
        )


@pytest.mark.asyncio
async def test_geographic_inheritance_and_public_scores(environment):
    ctx, country, _ = environment
    async with ctx["sessions"]() as session, session.begin():
        city = Location(
            name="Nadi", canonical_name="nadi", type="city", country_code="FJ", parent_id=country
        )
        sibling = Location(
            name="Suva", canonical_name="suva", type="city", country_code="FJ", parent_id=country
        )
        session.add_all([city, sibling])
        await session.flush()
        district = Location(
            name="Harbour",
            canonical_name="harbour",
            type="neighborhood",
            country_code="FJ",
            parent_id=city.id,
        )
        session.add(district)
        await session.flush()
    ancestors = await seed(ctx, country)
    exact = await seed(ctx, city.id)
    child = await seed(ctx, district.id)
    other = await seed(ctx, sibling.id)
    service = IntelligenceService(ctx["sessions"])
    page = await service.search_location_scams(city.id, as_of=NOW)
    assert page.total == 3 and page.outcome == "findings"
    assert {item.id: item.scope for item in page.items} == {
        ancestors[0]: "ancestor",
        exact[0]: "exact",
        child[0]: "descendant",
    }
    assert other[0] not in [item.id for item in page.items]
    assert page.items[0].id == exact[0]
    assert page.research.status == "never_researched"
    card = page.items[0]
    assert card.location.id == city.id and card.location_path[0].id == country
    assert card.title == SUMMARY and card.category == "transport"
    assert card.metrics.status == "current" and 0 < card.metrics.confidence_score < 0.99
    assert card.metrics.all_time.raw_report_count == 1
    assert card.metrics.occurrence_windows["30"].raw_report_count == 1
    assert "PRIVATE" not in page.model_dump_json()
    narrow = await service.search_location_scams(
        city.id, inherit_parents=False, include_descendants=False, as_of=NOW
    )
    assert narrow.total == 1
    summary = await service.get_location_summary(city.id, as_of=NOW)
    assert (summary.exact_patterns, summary.inherited_patterns, summary.descendant_patterns) == (
        1,
        1,
        1,
    )
    assert summary.categories == {"transport": 3}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        {"sensitive": True},
        {"sensitive": "false"},
        {"entities": ["Named business"]},
        {"publication_status": "needs_review"},
        {"evidence_quote": "invented ungrounded quote"},
        {"extraction": {"clean_text_hash": "0" * 64}},
    ],
)
async def test_held_or_ungrounded_support_is_indistinguishable_from_missing(environment, data):
    ctx, location, _ = environment
    held = await seed(ctx, location, data=data)
    service = IntelligenceService(ctx["sessions"])
    assert (await service.search_location_scams(location)).total == 0
    for identity in (held[0], uuid4()):
        with pytest.raises(IntelligenceNotFound, match="hypothesis_not_found"):
            await service.get_hypothesis(identity)
        with pytest.raises(IntelligenceNotFound):
            await service.get_hypothesis_evidence(identity)


@pytest.mark.asyncio
async def test_private_evidence_does_not_change_public_scores_or_expose_report_identity(
    environment,
):
    ctx, location, _ = environment
    public = await seed(ctx, location)
    service = IntelligenceService(ctx["sessions"])
    before = await service.get_hypothesis(public[0], as_of=NOW)
    await seed(ctx, location, hypothesis_id=public[0], data={"sensitive": True})
    assert (await service.get_hypothesis(public[0], as_of=NOW)).metrics == before.metrics
    report = await seed(ctx, location, hypothesis_id=public[0], report=True)
    evidence = await service.get_hypothesis_evidence(public[0], as_of=NOW)
    assert evidence.total == 2 and evidence.metrics_status == "current"
    anonymous = next(item for item in evidence.items if item.id == report[2])
    assert (
        anonymous.label == "anonymous_traveler_report"
        and anonymous.provenance_class == "SECOND_HAND"
    )
    assert anonymous.claim is None and anonymous.excerpt is None and anonymous.source_url is None
    assert anonymous.published_at is None and anonymous.platform is None
    serialized = evidence.model_dump_json()
    for private in (
        str(report[1]),
        str(report[3]),
        str(report[4]),
        str(report[5]),
        "PRIVATE",
        "private-author",
        "private/",
    ):
        assert private not in serialized
    assert all(item.evidence_weight is not None for item in evidence.items)
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(ResearchJob)) == 0
        assert (await session.get(Hypothesis, public[0])).confidence_score == 0.99
    ctx["provider"].search.assert_not_called()
    ctx["llm"].complete.assert_not_called()


@pytest.mark.asyncio
async def test_research_outcomes_expiry_and_private_scheduler_metadata(environment):
    ctx, location, _ = environment
    service = IntelligenceService(ctx["sessions"])
    empty = await service.search_location_scams(location, as_of=NOW)
    assert empty.outcome == "research_incomplete" and empty.research.status == "never_researched"
    async with ctx["sessions"]() as session, session.begin():
        session.add(
            LocationResearchState(
                location_id=location,
                status="ready",
                initial_completed_at=NOW,
                last_success_at=NOW,
                fresh_until=NOW + timedelta(hours=1),
                progress={"complete": True, "private_internal_progress": "secret"},
            )
        )
    ready = await service.search_location_scams(location, as_of=NOW)
    assert ready.outcome == "nothing_found" and ready.research.complete
    assert "secret" not in ready.model_dump_json()
    assert "progress" not in type(ready.research).model_fields
    stale = await service.get_location_summary(location, as_of=NOW + timedelta(days=1))
    assert stale.research.status == "stale" and stale.research.stale
    assert stale.outcome == "research_incomplete"
    async with ctx["sessions"]() as session:
        assert (await session.get(LocationResearchState, location)).status == "ready"
        assert await session.scalar(select(func.count()).select_from(ResearchJob)) == 0


@pytest.mark.asyncio
async def test_attachment_hold_and_evidence_limit_fail_closed(environment):
    ctx, location, _ = environment
    report = await seed(ctx, location, report=True)
    service = IntelligenceService(ctx["sessions"])
    async with ctx["sessions"]() as session, session.begin():
        attachment = ReportAttachment(
            report_id=report[5],
            upload_key_hash="1" * 64,
            original_hash="2" * 64,
            stored_hash="3" * 64,
            original_size=10,
            stored_size=10,
            width=1,
            height=1,
            media_type="image/png",
            raw_object_path="private/raw",
            private_object_path="private/image",
        )
        session.add(attachment)
        await session.flush()
        attachment_id = attachment.id
    assert (await service.search_location_scams(location)).total == 0
    async with ctx["sessions"]() as session, session.begin():
        (await session.get(ReportAttachment, attachment_id)).status = "accepted"
    assert (await service.search_location_scams(location)).total == 1
    # Overflow must not calculate an apparently complete score from the first 200 records.
    for _ in range(200):
        await seed(ctx, location, hypothesis_id=report[0])
    card = await service.get_hypothesis(report[0], as_of=NOW)
    assert card.metrics.status == "unavailable" and card.metrics.confidence_score is None
    assert "public_evidence_limit" in card.metrics.limitations
    page = await service.get_hypothesis_evidence(report[0], offset=200, limit=1, as_of=NOW)
    assert page.total == 201 and len(page.items) == 1 and not page.has_more
    assert page.metrics_status == "unavailable" and page.items[0].evidence_weight is None


@pytest.mark.asyncio
async def test_pagination_literal_search_and_occurrence_dates(environment):
    ctx, location, _ = environment
    recent = await seed(ctx, location)
    await seed(ctx, location, occurred_at=None)
    await seed(ctx, location, occurred_at=NOW - timedelta(days=100))
    await seed(ctx, location, occurred_at=NOW + timedelta(days=1))
    await seed(ctx, location, status="inactive")
    service = IntelligenceService(ctx["sessions"])
    page = await service.search_location_scams(location, limit=2, as_of=NOW)
    second = await service.search_location_scams(location, limit=2, offset=2, as_of=NOW)
    assert page.total == 4 and page.has_more and not second.has_more
    assert {i.id for i in page.items}.isdisjoint({i.id for i in second.items})
    assert (await service.search_scams(location, "%", as_of=NOW)).total == 0
    assert (await service.search_scams(location, "PRIVATE", as_of=NOW)).total == 0
    assert (
        await service.search_scams(location, "driver", category="transport", as_of=NOW)
    ).total == 4
    patterns = await service.get_recent_patterns(location, days=30, as_of=NOW)
    assert [item.id for item in patterns.items] == [recent[0]]
    for options in ({"limit": 21}, {"offset": -1}, {"limit": True}, {"inherit_parents": "yes"}):
        with pytest.raises(IntelligenceInputError):
            await service.search_location_scams(location, **options)
    with pytest.raises(IntelligenceNotFound):
        await service.get_location_summary(uuid4())


@pytest.mark.asyncio
async def test_related_direction_and_hidden_targets(environment):
    ctx, location, _ = environment
    parent, child = await seed(ctx, location), await seed(ctx, location)
    held = await seed(ctx, location, data={"sensitive": True})
    async with ctx["sessions"]() as session, session.begin():
        session.add_all(
            [
                HypothesisRelation(
                    source_hypothesis_id=child[0],
                    target_hypothesis_id=parent[0],
                    relation_type="SUBTYPE_OF",
                ),
                HypothesisRelation(
                    source_hypothesis_id=child[0],
                    target_hypothesis_id=held[0],
                    relation_type="RELATED_TO",
                ),
            ]
        )
    service = IntelligenceService(ctx["sessions"])
    outgoing = await service.get_related_hypotheses(child[0], as_of=NOW)
    incoming = await service.get_related_hypotheses(parent[0], as_of=NOW)
    assert outgoing.total == 1 and outgoing.items[0].direction == "outgoing"
    assert incoming.total == 1 and incoming.items[0].direction == "incoming"


@pytest.mark.asyncio
async def test_invalid_legacy_metadata_returns_unknown_without_breaking_evidence_read(environment):
    ctx, location, _ = environment
    public = await seed(ctx, location)
    async with ctx["sessions"]() as session, session.begin():
        (await session.get(Source, public[3])).source_metadata = ["invalid legacy metadata"]
    service = IntelligenceService(ctx["sessions"])
    card = await service.get_hypothesis(public[0], as_of=NOW)
    assert card.metrics.status == "unavailable"
    assert "invalid_evidence_metadata" in card.metrics.limitations
    evidence = await service.get_hypothesis_evidence(public[0], as_of=NOW)
    assert evidence.total == 1 and evidence.items[0].evidence_weight is None


@pytest.mark.asyncio
async def test_incident_matching_local_public_only_and_explicit_unavailability(environment):
    ctx, location, _ = environment
    incident = "A driver changed the agreed fare and demanded extra cash."
    empty = IntelligenceService(ctx["sessions"])
    assert (await empty.match_incident(location, incident, as_of=NOW)).status == "no_match"
    public = await seed(ctx, location)
    await seed(ctx, location, data={"sensitive": True})
    assert (await empty.match_incident(location, incident, as_of=NOW)).status == "unavailable"
    ctx["embedder"].embed.side_effect = lambda texts: [[1.0] + [0.0] * 383 for _ in texts]
    service = IntelligenceService(ctx["sessions"], embedder=ctx["embedder"])
    result = await service.match_incident(location, incident, as_of=NOW)
    assert result.status == "matched" and result.complete
    assert result.items[0].hypothesis.id == public[0] and result.items[0].similarity == 1
    assert ctx["embedder"].embed.call_args.args[0] == [incident, SUMMARY]
    assert incident not in result.model_dump_json()
    ctx["embedder"].preload.side_effect = OSError("cache missing")
    failed = await service.match_incident(location, incident, as_of=NOW)
    assert failed.status == "unavailable" and not failed.complete
    ctx["llm"].complete.assert_not_called()
