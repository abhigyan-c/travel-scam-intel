"""Validated HTTP inputs and the existing correlated error envelope."""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ErrorDetail(BaseModel):
    field: list[str | int]
    type: str


class APIError(BaseModel):
    code: str
    message: str
    request_id: str
    details: list[ErrorDetail] | None = None


class ErrorEnvelope(BaseModel):
    error: APIError


class PageQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    limit: int = Field(default=20, ge=1, le=20, description="Maximum records per page.")
    offset: int = Field(default=0, ge=0, le=1000, description="Offset into the filtered result.")


class LocationQuery(PageQuery):
    q: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Literal substring of a public supporting summary.",
    )
    category: str | None = Field(default=None, min_length=1, max_length=100)
    inherit_parents: bool = Field(default=True, description="Include labeled ancestor patterns.")
    include_descendants: bool = Field(
        default=True, description="Include this location's descendants."
    )
    research: bool = Field(
        default=True, description="Request background research if needed; never wait."
    )


class RecentQuery(LocationQuery):
    days: int = Field(
        default=30,
        json_schema_extra={"enum": [30, 90, 365]},
        description="Occurrence window in days, not publication recency.",
    )

    @field_validator("days")
    @classmethod
    def supported_days(cls, value):
        if value not in (30, 90, 365):
            raise ValueError("unsupported_occurrence_window")
        return value


class SummaryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    research: bool = Field(
        default=True, description="Request background research if needed; never wait."
    )


class HypothesisQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested_location_id: UUID | None = Field(
        default=None, description="Context for scope labeling."
    )


class IncidentMatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    location_id: UUID
    incident: Annotated[str, Field(min_length=20, max_length=2000)]
    limit: int = Field(default=5, ge=1, le=20, strict=True)
    min_similarity: float = Field(default=0.7, ge=0, le=1, allow_inf_nan=False, strict=True)
