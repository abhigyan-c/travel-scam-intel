import re
import unicodedata
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.locations import normalize_name
from app.models import Claim, Location, LocationAlias
from app.models.location import LocationType

RESOLUTION_VERSION = "location-v1"


class LocationNode(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    type: LocationType
    country_code: str | None
    is_provisional: bool


class LocationResolution(BaseModel):
    version: str = RESOLUTION_VERSION
    text: str | None
    normalized_name: str | None = None
    status: Literal["resolved", "provisional", "ambiguous", "unresolved"]
    location_id: UUID | None = None
    candidates: list[UUID] = Field(default_factory=list)
    total: int = 0
    hierarchy: list[LocationNode] = Field(default_factory=list)
    reason: str | None = None
    created: bool = False


def _display_name(value: str) -> str:
    name = " ".join(unicodedata.normalize("NFKC", value).split())
    if not name or len(name) > 200 or len(normalize_name(name)) > 200:
        raise ValueError("name must contain 1–200 normalized characters")
    if any(unicodedata.category(char).startswith("C") for char in name):
        raise ValueError("name contains control characters")
    return name


def normalize_entities(values: list[str]) -> list[dict[str, str]]:
    if not isinstance(values, list) or len(values) > 100:
        raise ValueError("entities must be a bounded list of names")
    result, seen = [], set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError("entity names must be strings")
        name = _display_name(value)
        normalized = normalize_name(name)
        if normalized not in seen:
            result.append({"name": name, "normalized_name": normalized})
            seen.add(normalized)
    return result


async def get_location_path(session: AsyncSession, location_id: UUID) -> list[Location]:
    path, seen = [], set()
    current = location_id
    while current is not None:
        if current in seen or len(path) >= 32:
            raise ValueError("cyclic_or_excessive_location_hierarchy")
        seen.add(current)
        row = await session.get(Location, current)
        if row is None:
            raise ValueError("location_not_found")
        path.append(row)
        current = row.parent_id
    countries = {row.country_code for row in path if row.country_code is not None}
    if len(countries) > 1:
        raise ValueError("conflicting_country_hierarchy")
    ordered = list(reversed(path))
    for parent, child in zip(ordered, ordered[1:], strict=False):
        if (
            parent.type == LocationType.POINT_OF_INTEREST
            and child.type != LocationType.POINT_OF_INTEREST
        ):
            raise ValueError("incompatible_location_hierarchy")
    return ordered


def _descendants(root_id: UUID):
    scope = select(Location.id).where(Location.id == root_id).cte(recursive=True)
    # UNION, rather than UNION ALL, also bounds traversal of malformed legacy cycles.
    return scope.union(select(Location.id).join(scope, Location.parent_id == scope.c.id))


async def _resolved(
    session: AsyncSession, row: Location, text: str, normalized: str, *, created: bool = False
) -> LocationResolution:
    path = await get_location_path(session, row.id)
    return LocationResolution(
        text=text,
        normalized_name=normalized,
        status="provisional" if row.is_provisional else "resolved",
        location_id=row.id,
        candidates=[row.id],
        total=1,
        hierarchy=[LocationNode.model_validate(item) for item in path],
        created=created,
    )


async def resolve_location(
    session: AsyncSession,
    text: str | None,
    *,
    parent_id: UUID | None = None,
    country_code: str | None = None,
    location_type: LocationType | None = None,
    create_provisional: bool = True,
) -> LocationResolution:
    if country_code is not None:
        country_code = country_code.upper()
        if not re.fullmatch("[A-Z]{2}", country_code):
            raise ValueError("invalid_country_code")
    if location_type is not None:
        location_type = LocationType(location_type)
    parent_path = await get_location_path(session, parent_id) if parent_id else []
    inherited_code = next((row.country_code for row in parent_path if row.country_code), None)
    if country_code and inherited_code and country_code != inherited_code:
        raise ValueError("country_context_conflict")
    if country_code and parent_path and inherited_code is None:
        raise ValueError("country_context_unverified")
    if text is None:
        return LocationResolution(text=None, status="unresolved", reason="missing_location")
    name = _display_name(text)
    normalized = normalize_name(name)
    alias = (
        select(LocationAlias.id)
        .where(
            LocationAlias.location_id == Location.id, LocationAlias.normalized_alias == normalized
        )
        .exists()
    )
    name_filter = or_(Location.canonical_name == normalized, alias)
    global_total = await session.scalar(
        select(func.count()).select_from(Location).where(name_filter)
    )
    filters = [name_filter]
    if location_type is not None:
        filters.append(Location.type == location_type)
    country = None
    if country_code is not None:
        countries = list(
            await session.scalars(
                select(Location)
                .where(Location.type == LocationType.COUNTRY, Location.country_code == country_code)
                .limit(2)
            )
        )
        if len(countries) != 1:
            return LocationResolution(
                text=text,
                normalized_name=normalized,
                status="unresolved",
                reason="unknown_or_ambiguous_country",
            )
        country = countries[0]
        await get_location_path(session, country.id)
        scope = _descendants(country.id)
        filters.append(Location.id.in_(select(scope.c.id)))
    if parent_id is not None:
        scope = _descendants(parent_id)
        filters.append(
            or_(
                Location.id.in_(select(scope.c.id)),
                Location.id.in_([row.id for row in parent_path]),
            )
        )
    total = await session.scalar(select(func.count()).select_from(Location).where(*filters))
    matches = list(
        await session.scalars(select(Location).where(*filters).order_by(Location.id).limit(20))
    )
    if total == 1:
        return await _resolved(session, matches[0], text, normalized)
    if total:
        return LocationResolution(
            text=text,
            normalized_name=normalized,
            status="ambiguous",
            candidates=[row.id for row in matches],
            total=total,
            reason="multiple_matches",
        )
    if global_total:
        return LocationResolution(
            text=text,
            normalized_name=normalized,
            status="unresolved",
            reason="conflicting_context_or_type",
        )
    creation_parent = parent_path[-1] if parent_path else country
    if not create_provisional or location_type is None or creation_parent is None:
        return LocationResolution(
            text=text,
            normalized_name=normalized,
            status="unresolved",
            reason="insufficient_context_or_creation_disabled",
        )
    if location_type == LocationType.COUNTRY:
        return LocationResolution(
            text=text,
            normalized_name=normalized,
            status="unresolved",
            reason="country_requires_root_review",
        )
    if (
        creation_parent.type == LocationType.POINT_OF_INTEREST
        and location_type != LocationType.POINT_OF_INTEREST
    ):
        return LocationResolution(
            text=text,
            normalized_name=normalized,
            status="unresolved",
            reason="incompatible_parent_type",
        )
    creation_path = await get_location_path(session, creation_parent.id)
    code = next((row.country_code for row in creation_path if row.country_code), None)
    identity = await session.scalar(
        insert(Location)
        .values(
            name=name,
            canonical_name=normalized,
            type=location_type,
            parent_id=creation_parent.id,
            country_code=code,
            is_provisional=True,
        )
        .on_conflict_do_nothing(constraint="uq_location_identity")
        .returning(Location.id)
    )
    created = identity is not None
    if identity is None:
        identity = await session.scalar(
            select(Location.id).where(
                Location.parent_id == creation_parent.id,
                Location.canonical_name == normalized,
                Location.type == location_type,
            )
        )
    row = await session.get(Location, identity)
    return await _resolved(session, row, text, normalized, created=created)


async def resolve_claim(
    session: AsyncSession, claim_id: UUID, **context
) -> LocationResolution | None:
    claim = await session.scalar(select(Claim).where(Claim.id == claim_id).with_for_update())
    if claim is None:
        return None
    metadata = claim.structured_data
    entities = normalize_entities(metadata.get("entities", []))
    text = metadata.get("unresolved_location", metadata.get("location"))
    if text is not None and not isinstance(text, str):
        raise ValueError("location text must be a string")
    resolution = await resolve_location(session, text, **context)
    await session.execute(
        update(Claim)
        .where(Claim.id == claim_id)
        .values(
            location_id=resolution.location_id,
            structured_data=Claim.structured_data.op("||")(
                {
                    "location_resolution": resolution.model_dump(mode="json"),
                    "normalized_entities": entities,
                }
            ),
        )
    )
    return resolution
