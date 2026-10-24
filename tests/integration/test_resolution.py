import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.db.locations import find_locations, normalize_name
from app.db.resolution import get_location_path, resolve_claim, resolve_location
from app.models import Claim, Document, Location, LocationAlias, Source
from app.models.location import LocationType
from app.models.source import SourceType

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]


async def known(session, name):
    return (await find_locations(session, name, exact=True))[0][0]


@pytest.mark.parametrize(
    "query,names",
    [
        ("ＢＫＫ", ["Thailand", "Bangkok"]),
        ("  Grand   Palace ", ["Thailand", "Bangkok", "Grand Palace"]),
        ("Nadi", ["Fiji", "Nadi"]),
    ],
)
async def test_known_aliases_and_hierarchies(session, query, names):
    result = await resolve_location(session, query)
    assert result.status == "resolved" and not result.created
    assert [row.name for row in result.hierarchy] == names
    assert result.text == query and result.location_id == result.hierarchy[-1].id


async def test_unknown_provisional_is_repeatable_and_has_no_invented_coordinates(session):
    bangkok = await known(session, "Bangkok")
    name = f"Fixture market {uuid4()}"
    result = await resolve_location(
        session, name, parent_id=bangkok.id, country_code="th", location_type=LocationType.AREA
    )
    assert result.status == "provisional" and result.created
    row = await session.get(Location, result.location_id)
    assert row.is_provisional and row.parent_id == bangkok.id and row.country_code == "TH"
    assert row.latitude is None and row.longitude is None
    repeated = await resolve_location(
        session, name.upper(), parent_id=bangkok.id, location_type=LocationType.AREA
    )
    assert repeated.location_id == row.id and not repeated.created
    assert (
        await session.scalar(
            select(func.count())
            .select_from(Location)
            .where(Location.canonical_name == normalize_name(name))
        )
        == 1
    )


async def test_country_context_can_parent_an_explicit_unknown_city(session):
    result = await resolve_location(
        session, f"New city {uuid4()}", country_code="FJ", location_type=LocationType.CITY
    )
    assert result.status == "provisional"
    assert [row.name for row in result.hierarchy][0] == "Fiji"


async def test_insufficient_context_and_missing_text_do_not_create_locations(session):
    before = await session.scalar(select(func.count()).select_from(Location))
    for kwargs in [{}, {"location_type": LocationType.CITY}, {"country_code": "TH"}]:
        result = await resolve_location(session, f"Unknown {uuid4()}", **kwargs)
        assert result.status == "unresolved" and result.location_id is None
    result = await resolve_location(session, None)
    assert result.reason == "missing_location"
    bangkok = await known(session, "Bangkok")
    result = await resolve_location(
        session,
        str(uuid4()),
        parent_id=bangkok.id,
        location_type=LocationType.AREA,
        create_provisional=False,
    )
    assert result.status == "unresolved"
    assert await session.scalar(select(func.count()).select_from(Location)) == before


async def test_shared_alias_is_ambiguous_until_country_or_ancestor_disambiguates(session):
    bangkok, nadi = await known(session, "Bangkok"), await known(session, "Nadi")
    alias = f"Shared landmark {uuid4()}"
    places = []
    for parent in (bangkok, nadi):
        row = Location(
            name=str(uuid4()),
            canonical_name=str(uuid4()),
            parent_id=parent.id,
            country_code=parent.country_code,
            type=LocationType.POINT_OF_INTEREST,
        )
        session.add(row)
        await session.flush()
        session.add(
            LocationAlias(location_id=row.id, alias=alias, normalized_alias=normalize_name(alias))
        )
        places.append(row)
    await session.flush()
    ambiguous = await resolve_location(session, alias)
    assert ambiguous.status == "ambiguous" and ambiguous.location_id is None
    assert set(ambiguous.candidates) == {row.id for row in places}
    scoped = await resolve_location(session, alias, parent_id=bangkok.id, country_code="TH")
    assert scoped.location_id == places[0].id
    assert (await resolve_location(session, alias, country_code="FJ")).location_id == places[1].id


async def test_ancestor_scope_handles_deeper_places_and_known_ancestors(session):
    bangkok = await known(session, "Bangkok")
    area = await resolve_location(
        session, str(uuid4()), parent_id=bangkok.id, location_type=LocationType.AREA
    )
    poi = await resolve_location(
        session,
        str(uuid4()),
        parent_id=area.location_id,
        location_type=LocationType.POINT_OF_INTEREST,
    )
    found = await resolve_location(session, poi.text, parent_id=bangkok.id, country_code="TH")
    assert found.location_id == poi.location_id
    assert (
        await resolve_location(session, "BKK", parent_id=poi.location_id)
    ).location_id == bangkok.id


async def test_known_elsewhere_never_creates_a_guessed_local_copy(session):
    bangkok = await known(session, "Bangkok")
    before = await session.scalar(select(func.count()).select_from(Location))
    result = await resolve_location(
        session, "Nadi", parent_id=bangkok.id, location_type=LocationType.CITY
    )
    assert result.status == "unresolved" and result.reason == "conflicting_context_or_type"
    assert await session.scalar(select(func.count()).select_from(Location)) == before
    with pytest.raises(ValueError, match="country_context_conflict"):
        await resolve_location(session, "Nadi", parent_id=bangkok.id, country_code="FJ")


async def test_invalid_missing_and_cyclic_context_is_rejected(session):
    with pytest.raises(ValueError, match="not_found"):
        await resolve_location(session, "Unknown", parent_id=uuid4())
    a = Location(name="Cycle A", canonical_name=str(uuid4()), type=LocationType.AREA)
    session.add(a)
    await session.flush()
    b = Location(
        name="Cycle B", canonical_name=str(uuid4()), type=LocationType.AREA, parent_id=a.id
    )
    session.add(b)
    await session.flush()
    a.parent_id = b.id
    await session.flush()
    with pytest.raises(ValueError, match="cyclic"):
        await get_location_path(session, a.id)


async def test_claim_resolution_preserves_raw_fields_and_sensitive_hold(session):
    source = Source(platform="fixture", source_type=SourceType.USER_REPORT)
    session.add(source)
    await session.flush()
    document = Document(source_id=source.id, raw_object_path="raw/fixture", content_hash="a" * 64)
    session.add(document)
    await session.flush()
    claim = Claim(
        document_id=document.id,
        claim_text="Fixture claim",
        normalized_claim="fixture claim",
        claim_type="fare",
        structured_data={
            "unresolved_location": "ＢＫＫ",
            "location": "ＢＫＫ",
            "entities": [" Acme  Tours ", "ACME Tours"],
            "publication_status": "held",
            "first_hand": None,
        },
    )
    session.add(claim)
    await session.flush()
    result = await resolve_claim(session, claim.id)
    await session.refresh(claim)
    assert claim.location_id == result.location_id
    assert claim.structured_data["unresolved_location"] == "ＢＫＫ"
    assert claim.structured_data["publication_status"] == "held"
    assert claim.structured_data["entities"] == [" Acme  Tours ", "ACME Tours"]
    assert claim.structured_data["normalized_entities"] == [
        {"name": "Acme Tours", "normalized_name": "acme tours"}
    ]
    assert claim.document_id == document.id
    claim.structured_data = {**claim.structured_data, "unresolved_location": "Unidentified station"}
    await session.flush()
    unresolved = await resolve_claim(session, claim.id)
    await session.refresh(claim)
    assert unresolved.status == "unresolved" and claim.location_id is None
    assert claim.structured_data["unresolved_location"] == "Unidentified station"
    assert await resolve_claim(session, uuid4()) is None


async def test_unverified_country_and_incompatible_parent_cannot_create(session):
    root = Location(name="Unplaced area", canonical_name=str(uuid4()), type=LocationType.AREA)
    session.add(root)
    await session.flush()
    with pytest.raises(ValueError, match="unverified"):
        await resolve_location(
            session,
            "Unknown",
            parent_id=root.id,
            country_code="TH",
            location_type=LocationType.CITY,
        )
    palace = await known(session, "Grand Palace")
    result = await resolve_location(
        session, str(uuid4()), parent_id=palace.id, location_type=LocationType.CITY
    )
    assert result.status == "unresolved" and result.reason == "incompatible_parent_type"
    result = await resolve_location(
        session, str(uuid4()), parent_id=root.id, location_type=LocationType.COUNTRY
    )
    assert result.status == "unresolved" and result.reason == "country_requires_root_review"


async def test_country_conflicts_in_existing_hierarchy_are_rejected(session):
    nadi = await known(session, "Nadi")
    row = Location(
        name="Contradictory area",
        canonical_name=str(uuid4()),
        type=LocationType.AREA,
        parent_id=nadi.id,
        country_code="TH",
    )
    session.add(row)
    await session.flush()
    with pytest.raises(ValueError, match="conflicting_country_hierarchy"):
        await get_location_path(session, row.id)
