from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.location import LocationType


class AliasRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    alias: str


class LocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    canonical_name: str
    type: LocationType
    parent_id: UUID | None
    country_code: str | None
    latitude: float | None
    longitude: float | None
    is_provisional: bool
    created_at: datetime
    updated_at: datetime
    aliases: list[AliasRead]


class LocationSearch(BaseModel):
    items: list[LocationRead]
    total: int
    limit: int
    offset: int


class LocationResolution(BaseModel):
    status: Literal["resolved", "ambiguous", "not_found"]
    matches: list[LocationRead]
    total: int
