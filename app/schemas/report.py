from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.location import LocationType
from app.models.user_report import ReportProvenance


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_text: str = Field(min_length=20, max_length=8000)
    provenance_class: ReportProvenance
    occurred_at: datetime | None = None
    location_id: UUID | None = None
    location_text: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: UUID | None = None
    country_code: str | None = Field(default=None, pattern="^[A-Z]{2}$")
    location_type: LocationType | None = None

    @field_validator("occurred_at")
    @classmethod
    def aware_date(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timezone_required")
        return value

    @model_validator(mode="after")
    def geographic_context(self):
        if self.location_id is not None and any(
            value is not None
            for value in (self.location_text, self.parent_id, self.country_code, self.location_type)
        ):
            raise ValueError("use_location_id_or_text_context")
        if self.location_text is None and any(
            value is not None for value in (self.parent_id, self.country_code, self.location_type)
        ):
            raise ValueError("location_text_required_with_context")
        return self


class ReportReceipt(BaseModel):
    report_id: UUID
    status: str
    replayed: bool
    queue_pending: bool
    message: str
