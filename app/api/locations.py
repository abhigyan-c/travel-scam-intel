from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.locations import find_locations, get_location, normalize_name
from app.schemas.location import LocationRead, LocationResolution, LocationSearch

router = APIRouter(prefix="/v1/locations", tags=["locations"])


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db_sessions() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]
LocationQuery = Annotated[str, Query(min_length=1, max_length=200)]
CountryCode = Annotated[str | None, Query(pattern="^[A-Z]{2}$")]


def validate_query(query: str) -> None:
    if not normalize_name(query):
        raise HTTPException(422, "Location query must not be blank")


@router.get("", response_model=LocationSearch)
async def search_locations(
    session: Session,
    q: LocationQuery,
    country_code: CountryCode = None,
    parent_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    validate_query(q)
    items, total = await find_locations(
        session, q, country_code=country_code, parent_id=parent_id, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/resolve", response_model=LocationResolution)
async def resolve_location(
    session: Session,
    q: LocationQuery,
    country_code: CountryCode = None,
    parent_id: UUID | None = None,
):
    validate_query(q)
    items, total = await find_locations(
        session, q, exact=True, country_code=country_code, parent_id=parent_id
    )
    status = "not_found" if total == 0 else "resolved" if total == 1 else "ambiguous"
    return {"status": status, "matches": items, "total": total}


@router.get("/{location_id}", response_model=LocationRead)
async def read_location(session: Session, location_id: UUID):
    location = await get_location(session, location_id)
    if location is None:
        raise HTTPException(404, "Location not found")
    return location
