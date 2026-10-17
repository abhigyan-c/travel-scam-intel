import os
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.api.locations import get_session
from app.core.config import Settings
from app.db.locations import find_locations, normalize_name
from app.db.seed import SEED_LOCATIONS, seed_locations
from app.main import create_app
from app.models.location import Location, LocationAlias, LocationType
from app.models.source import Source, SourceType

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]


@pytest.fixture
async def client(session):
    application = create_app(Settings(_env_file=None))

    async def use_session():
        yield session

    application.dependency_overrides[get_session] = use_session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        yield client


async def test_seed_is_repeatable_and_preserves_hierarchy(session):
    connection = await session.connection()
    before = await session.scalar(select(func.count()).select_from(Location))
    for _ in range(2):
        await connection.run_sync(seed_locations)
    assert await session.scalar(select(func.count()).select_from(Location)) == before
    seeded = {}
    for name, parent, code, location_type in SEED_LOCATIONS:
        rows, total = await find_locations(session, name, exact=True)
        assert total == 1
        location = rows[0]
        assert location.parent_id == (seeded[parent].id if parent else None)
        assert location.country_code == code
        assert location.type == location_type
        assert not location.is_provisional
        seeded[name] = location
    rows, total = await find_locations(session, " ＢＫＫ ", exact=True)
    assert total == 1 and rows[0].id == seeded["Bangkok"].id
    assert [alias.alias for alias in rows[0].aliases] == ["BKK"]


async def test_location_api_resolves_alias_and_reads_parent_chain(client):
    response = await client.get("/v1/locations/resolve", params={"q": " bKk "})
    assert response.status_code == 200
    assert response.json()["status"] == "resolved"
    bangkok = response.json()["matches"][0]
    assert bangkok["name"] == "Bangkok"
    response = await client.get("/v1/locations", params={"q": "Grand", "parent_id": bangkok["id"]})
    palace = response.json()["items"][0]
    assert palace["name"] == "Grand Palace" and palace["parent_id"] == bangkok["id"]
    assert (await client.get(f"/v1/locations/{palace['id']}")).json() == palace
    thailand = (await client.get(f"/v1/locations/{bangkok['parent_id']}")).json()
    assert thailand["name"] == "Thailand" and thailand["parent_id"] is None
    assert (await client.get(f"/v1/locations/{uuid4()}")).status_code == 404
    unknown = (await client.get("/v1/locations/resolve", params={"q": str(uuid4())})).json()
    assert unknown == {"status": "not_found", "matches": [], "total": 0}


async def test_shared_aliases_are_ambiguous_and_filters_disambiguate(session, client):
    parents = {}
    for name in ("Thailand", "Fiji"):
        parents[name] = (await find_locations(session, name, exact=True))[0][0]
    name = f"Same place {uuid4()}"
    places = [
        Location(
            name=name,
            canonical_name=normalize_name(name),
            type=LocationType.CITY,
            parent_id=parents[parent].id,
            country_code=parents[parent].country_code,
            is_provisional=True,
        )
        for parent in ("Thailand", "Fiji")
    ]
    session.add_all(places)
    await session.flush()
    alias = f"shared {uuid4()}"
    session.add_all(
        [
            LocationAlias(location_id=place.id, alias=alias, normalized_alias=normalize_name(alias))
            for place in places
        ]
    )
    await session.flush()
    result = (await client.get("/v1/locations/resolve", params={"q": alias})).json()
    assert result["status"] == "ambiguous" and result["total"] == 2
    assert {item["id"] for item in result["matches"]} == {str(place.id) for place in places}
    result = (
        await client.get("/v1/locations/resolve", params={"q": alias, "country_code": "FJ"})
    ).json()
    assert result["status"] == "resolved" and result["matches"][0]["is_provisional"]
    page = (await client.get("/v1/locations", params={"q": alias, "limit": 1, "offset": 1})).json()
    assert page["total"] == 2 and len(page["items"]) == 1
    result = (
        await client.get(
            "/v1/locations/resolve", params={"q": name, "parent_id": str(parents["Thailand"].id)}
        )
    ).json()
    assert result["total"] == 1


async def test_search_does_not_duplicate_locations_or_treat_wildcards_as_sql(session):
    rows, _ = await find_locations(session, "Bangkok", exact=True)
    session.add(
        LocationAlias(location_id=rows[0].id, alias="Bangkok city", normalized_alias="bangkok city")
    )
    await session.flush()
    matches, total = await find_locations(session, "bangkok")
    assert total == 1 and len(matches) == 1
    for wildcard in ("%", "_"):
        matches, total = await find_locations(session, wildcard)
        assert total == 0 and matches == []


async def test_alias_identity_and_country_root_constraints(session):
    bangkok = (await find_locations(session, "Bangkok", exact=True))[0][0]
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(LocationAlias(location_id=bangkok.id, alias="bkk", normalized_alias="bkk"))
            await session.flush()
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                Location(
                    name="Nested country",
                    canonical_name="nested country",
                    type=LocationType.COUNTRY,
                    parent_id=bangkok.id,
                )
            )
            await session.flush()


@pytest.mark.parametrize(
    "invalid",
    [
        {"name": " "},
        {"canonical_name": " "},
        {"country_code": "zz"},
        {"latitude": 91, "longitude": 0},
        {"latitude": 0, "longitude": 181},
        {"latitude": 0},
        {"parent_id": uuid4()},
        {"type": "invalid"},
    ],
)
async def test_invalid_location_data_is_rejected(session, invalid):
    values = {"name": str(uuid4()), "canonical_name": str(uuid4()), "type": LocationType.CITY}
    values.update(invalid)
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(Location(**values))
            await session.flush()


async def test_self_parent_root_identity_and_parent_deletion_constraints(session):
    identity = uuid4()
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                Location(
                    id=identity,
                    parent_id=identity,
                    name="Self",
                    canonical_name="self",
                    type=LocationType.CITY,
                )
            )
            await session.flush()
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                Location(name="Thailand", canonical_name="thailand", type=LocationType.COUNTRY)
            )
            await session.flush()
    thailand = (await find_locations(session, "Thailand", exact=True))[0][0]
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(delete(Location).where(Location.id == thailand.id))


async def test_source_provenance_and_unique_underlying_url(session):
    published = datetime(2026, 1, 1, tzinfo=UTC)
    url = f"https://example.org/articles/{uuid4()}"
    source = Source(
        platform="example.org",
        canonical_url=url,
        author_identifier="author-123",
        source_type=SourceType.NEWS,
        published_at=published,
        source_metadata={"thread_id": "thread-123", "title": "Travel report"},
    )
    session.add(source)
    await session.flush()
    session.expire_all()
    stored = await session.scalar(select(Source).where(Source.canonical_url == url))
    assert stored.platform == "example.org" and stored.author_identifier == "author-123"
    assert stored.published_at == published and stored.retrieved_at.tzinfo is not None
    assert stored.source_metadata["thread_id"] == "thread-123"
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                Source(platform="example.org", canonical_url=url, source_type=SourceType.NEWS)
            )
            await session.flush()
    session.add_all(
        [Source(platform="traveler", source_type=SourceType.USER_REPORT) for _ in range(2)]
    )
    await session.flush()
