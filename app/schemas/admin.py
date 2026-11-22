from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.hypothesis_lifecycle import Draft


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    subject_id: UUID
    reason: str = Field(min_length=1, max_length=2000)
    confirmed: bool = Field(strict=True)

    @model_validator(mode="after")
    def confirmation_and_clock(self):
        if self.confirmed is not True:
            raise ValueError("confirmation_required")
        for key, value in self:
            if key.endswith("_at") and isinstance(value, datetime) and value.utcoffset() is None:
                raise ValueError("timezone_required")
        return self


class Versioned(Command):
    expected_updated_at: datetime


class Edit(Versioned):
    operation: Literal["edit_hypothesis"]
    draft: Draft


class Status(Versioned):
    operation: Literal["set_status"]
    status: Literal["candidate", "active", "disputed", "inactive"]


class Merge(Versioned):
    operation: Literal["merge"]
    target_id: UUID
    expected_target_at: datetime


class Move(Merge):
    operation: Literal["move_evidence"]
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)


class Split(Versioned):
    operation: Literal["split"]
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)
    draft: Draft


class ApplyResolution(Command):
    operation: Literal["apply_resolution"]


class RetryJob(Versioned):
    operation: Literal["retry_job"]


class Reprocess(Versioned):
    operation: Literal["reprocess_document"]


class Priority(Command):
    operation: Literal["set_priority"]
    expected_updated_at: datetime | None
    priority: int = Field(ge=-100, le=100, strict=True)


class Moderate(Command):
    operation: Literal["moderate_report"]
    expected_revision: int = Field(ge=0, strict=True)
    status: Literal["accepted", "rejected", "needs_review"]


class Emerging(Command):
    operation: Literal["emerging_decision"]
    revision: int = Field(ge=1, strict=True)
    action: Literal["acknowledged", "dismissed"]
    reason: str = Field(min_length=1, max_length=500)


AdminCommand = Annotated[
    Edit
    | Status
    | Merge
    | Move
    | Split
    | ApplyResolution
    | RetryJob
    | Reprocess
    | Priority
    | Moderate
    | Emerging,
    Field(discriminator="operation"),
]
