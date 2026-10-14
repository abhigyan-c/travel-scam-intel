import unicodedata
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.location import Location, LocationAlias


def normalize_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


async def find_locations(
    session: AsyncSession,
    query: str,
    *,
    exact: bool = False,
    country_code: str | None = None,
    parent_id: UUID | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Location], int]:
    normalized = normalize_name(query)
    if exact:
        name_match = Location.canonical_name == normalized
        alias_match = LocationAlias.normalized_alias == normalized
    else:
        name_match = Location.canonical_name.contains(normalized, autoescape=True)
        alias_match = LocationAlias.normalized_alias.contains(normalized, autoescape=True)
    aliases = (
        select(LocationAlias.id)
        .where(LocationAlias.location_id == Location.id, alias_match)
        .exists()
    )
    filters = [or_(name_match, aliases)]
    if country_code is not None:
        filters.append(Location.country_code == country_code)
    if parent_id is not None:
        filters.append(Location.parent_id == parent_id)
    total = await session.scalar(select(func.count()).select_from(Location).where(*filters))
    rows = await session.scalars(
        select(Location)
        .where(*filters)
        .options(selectinload(Location.aliases))
        .order_by(Location.canonical_name, Location.id)
        .limit(limit)
        .offset(offset)
    )
    return list(rows), total or 0


async def get_location(session: AsyncSession, location_id: UUID) -> Location | None:
    return await session.scalar(
        select(Location).where(Location.id == location_id).options(selectinload(Location.aliases))
    )
