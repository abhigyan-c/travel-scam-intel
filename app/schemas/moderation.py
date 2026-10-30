from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AttachmentReceipt(BaseModel):
    attachment_id: UUID
    report_id: UUID
    status: str
    moderation_revision: int
    replayed: bool


class ModerationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: Literal["accepted", "rejected", "needs_review"]
    expected_revision: int = Field(ge=0)
    actor: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=2000)


class ModerationReceipt(BaseModel):
    event_id: UUID
    report_id: UUID
    status: str
    moderation_revision: int
    replayed: bool
