import hashlib
import json
import os
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.db.resolution import normalize_name, resolve_claim, resolve_location
from app.extraction import ClaimBatch, ExtractedClaim
from app.integrations.sarvam import Completion
from app.models import Claim, Document, Location, LocationAlias, Source
from app.relevance import Relevance
from benchmarks.research import DATA, RecordedClient, load_corpus, run_case

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]


async def place(session, name):
    return await session.scalar(
        select(Location).where(Location.canonical_name == normalize_name(name))
    )


async def quoted_claim(session, location, *, held=True):
    quote = f"At {location}, a stranger falsely said the attraction was closed."
    source = Source(
        platform="fixture", canonical_url=f"https://example.org/{uuid4()}", source_type="other"
    )
    session.add(source)
    await session.flush()
    digest = hashlib.sha256(quote.encode()).hexdigest()
    document = Document(
        source_id=source.id,
        raw_object_path="raw/fixture",
        content_hash=digest,
        clean_text=quote,
        clean_text_hash=digest,
        language="en",
        processing_status="processed",
    )
    session.add(document)
    await session.flush()
    claim = Claim(
        document_id=document.id,
        claim_text="A traveler reports a false closure.",
        normalized_claim="a traveler reports a false closure.",
        claim_type="diversion",
        structured_data={
            "location": location,
            "unresolved_location": location,
            "evidence_quote": quote,
            "entities": [],
            "sensitive": held,
            "publication_status": "held" if held else "unreviewed",
            "extraction": {"clean_text_hash": digest, "quote_start": 0, "quote_end": len(quote)},
        },
    )
    session.add(claim)
    await session.flush()
    return claim, document


@pytest.mark.parametrize(
    "query",
    [
        "Grand Palace in Bangkok",
        "Grand Palace, Bangkok",
        "Near the Grand Palace in BKK",
        "Bangkok, Thailand",
    ],
)
async def test_qualified_names_reuse_known_hierarchy_and_preserve_input(session, query):
    result = await resolve_location(session, query, create_provisional=False)
    assert result.status == "resolved" and result.method == "qualified"
    assert result.text == query and not result.created
    assert result.hierarchy[0].name == "Thailand"
    assert result.hierarchy[1].name == "Bangkok"
    if "Palace" in query:
        assert result.hierarchy[-1].name == "Grand Palace"


async def test_named_quoted_landmark_is_provisional_repeatable_and_stays_held(session):
    bangkok = await place(session, "Bangkok")
    name = f"Fixture {uuid4().hex[:8]} Museum"
    text = name + " in Bangkok"
    claim, document = await quoted_claim(session, text)
    original = dict(claim.structured_data)
    result = await resolve_claim(session, claim.id, parent_id=bangkok.id, create_provisional=True)
    assert result.status == "provisional" and result.created
    assert result.version == "location-v2" and result.component_text == name
    row = await session.get(Location, result.location_id)
    assert row.parent_id == bangkok.id and row.type == "point_of_interest"
    assert row.country_code == "TH" and row.latitude is None and row.longitude is None
    again = await resolve_claim(session, claim.id, parent_id=bangkok.id, create_provisional=True)
    assert again.location_id == result.location_id and not again.created
    await session.refresh(claim)
    assert claim.document_id == document.id and claim.location_id == row.id
    for key in (
        "location",
        "unresolved_location",
        "evidence_quote",
        "extraction",
        "sensitive",
        "publication_status",
    ):
        assert claim.structured_data[key] == original[key]


@pytest.mark.parametrize(
    "query",
    [
        "the old station",
        "the old station in Bangkok",
        "a local market in Bangkok",
        "an unknown place in Bangkok",
        "a stranger at the palace in Bangkok",
        "Palace in Unknown City",
        "Grand Palace in Bangkok in Paris",
        "Grand Palace, Bangkok, Thailand",
    ],
)
async def test_missing_generic_or_unsupported_context_does_not_create_places(session, query):
    bangkok = await place(session, "Bangkok")
    claim, _ = await quoted_claim(session, query)
    before = await session.scalar(select(func.count()).select_from(Location))
    result = await resolve_claim(session, claim.id, parent_id=bangkok.id, create_provisional=True)
    assert result.status == "unresolved" and result.location_id is None
    assert await session.scalar(select(func.count()).select_from(Location)) == before


async def test_explicit_qualification_disambiguates_known_landmarks_but_not_parent_aliases(session):
    bangkok, nadi = await place(session, "Bangkok"), await place(session, "Nadi")
    name = f"Fixture {uuid4().hex[:8]} Palace"
    rows = []
    for parent in (bangkok, nadi):
        row = Location(
            name=name,
            canonical_name=normalize_name(name),
            type="point_of_interest",
            parent_id=parent.id,
            country_code=parent.country_code,
        )
        session.add(row)
        await session.flush()
        rows.append(row)
    assert (await resolve_location(session, name)).status == "ambiguous"
    qualified = await resolve_location(session, name + " in Bangkok")
    assert qualified.location_id == rows[0].id
    alias = f"Shared {uuid4().hex[:8]} City"
    for parent in (bangkok, nadi):
        session.add(
            LocationAlias(
                location_id=parent.id, alias=alias, normalized_alias=normalize_name(alias)
            )
        )
    await session.flush()
    result = await resolve_location(session, name + " in " + alias)
    assert result.location_id is None and result.reason == "qualified_parent_ambiguous"


@pytest.mark.parametrize("query", ["Grand Palace in Nadi", "Thailand in Bangkok"])
async def test_conflicting_or_inverted_hierarchy_is_not_reparented(session, query):
    result = await resolve_location(session, query)
    assert result.location_id is None


async def test_qualification_cannot_expand_the_callers_point_of_interest_scope(session):
    palace = await place(session, "Grand Palace")
    name = f"Fixture {uuid4().hex[:8]} Museum"
    before = await session.scalar(select(func.count()).select_from(Location))
    result = await resolve_location(session, name + " in Bangkok", parent_id=palace.id)
    assert result.location_id is None
    assert await session.scalar(select(func.count()).select_from(Location)) == before


@pytest.mark.parametrize(
    "fault",
    [
        "location_not_in_quote",
        "quote_not_in_document",
        "stale_hash",
        "wrong_offsets",
        "unprocessed_document",
    ],
)
async def test_ungrounded_or_stale_claim_location_is_withheld(session, fault):
    claim, document = await quoted_claim(session, "Grand Palace in Bangkok")
    data = dict(claim.structured_data)
    if fault == "location_not_in_quote":
        data["unresolved_location"] = "Grand Palace in Nadi"
    elif fault == "quote_not_in_document":
        document.clean_text = "This is an unrelated tourism page."
    elif fault == "stale_hash":
        data["extraction"] = {**data["extraction"], "clean_text_hash": "a" * 64}
    elif fault == "wrong_offsets":
        data["extraction"] = {**data["extraction"], "quote_start": 1}
    else:
        document.processing_status = "failed"
    claim.structured_data = data
    await session.flush()
    result = await resolve_claim(session, claim.id)
    assert result.location_id is None and result.reason == "ungrounded_location"


@pytest.mark.parametrize("sensitive", [False, True])
async def test_full_pipeline_creates_scoped_candidate_only_when_publicly_eligible(sensitive):
    case = next(c for c in load_corpus()["cases"] if c["id"] == "bangkok-landmark")
    client = AsyncMock()

    async def complete(**kwargs):
        data = Relevance(
            relevant=True,
            category="diversion",
            confidence=0.9,
            reason="False closure and shop diversion",
        )
        if kwargs["schema"] is not Relevance:
            data = ClaimBatch(
                claims=[
                    ExtractedClaim(
                        claim="A false palace closure and shop diversion were reported.",
                        evidence_quote=case["text"],
                        location="Grand Palace in Bangkok",
                        approach=[],
                        methods=["falsely said the palace was closed"],
                        warning_signs=[],
                        entities=[],
                        occurred_at=None,
                        first_hand=True,
                        confidence=0.9,
                        sensitive=sensitive,
                    )
                ]
            )
        return Completion(data=data, requests=1)

    client.complete.side_effect = complete
    rows = await run_case(
        case, RecordedClient({}, live=client), Settings().database_url.get_secret_value()
    )
    for row in rows:
        assert row["claims"] == row["resolved_claims"] == 1
        assert row["geography_check"] and row["lineage_check"]
        assert row["public_patterns"] == (0 if sensitive else 1)


async def test_previously_recorded_sensitive_claim_is_not_unheld_by_reprocessing():
    case = next(c for c in load_corpus()["cases"] if c["id"] == "bangkok-landmark")
    records = json.loads((DATA / "sarvam-recording.json").read_text())["records"]
    # Replay the previously observed sensitivity outcome as a controlled response,
    # not as a claimed live answer to the new prompt fingerprint.
    captured = next(
        row["completion"]["data"]
        for values in records.values()
        for row in values
        if row["completion"].get("data")
        and any(
            c.get("location") == "Grand Palace in Bangkok"
            for c in row["completion"]["data"].get("claims", [])
        )
    )
    client = AsyncMock()

    async def complete(**kwargs):
        data = (
            Relevance(
                relevant=True,
                category="diversion",
                confidence=0.9,
                reason="Source reports a diversion",
            )
            if kwargs["schema"] is Relevance
            else (ClaimBatch.model_validate(captured))
        )
        return Completion(data=data, requests=1)

    client.complete.side_effect = complete
    rows = await run_case(
        case, RecordedClient({}, live=client), Settings().database_url.get_secret_value()
    )
    assert all(r["geography_check"] and r["public_patterns"] == 0 for r in rows)
    assert rows[1]["llm_calls"] == 0
