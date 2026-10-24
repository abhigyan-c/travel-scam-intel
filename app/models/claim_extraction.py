from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ClaimExtraction(Base):
    __tablename__ = "claim_extractions"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "clean_text_hash",
            "model",
            "prompt_version",
            "passage_start",
            "passage_end",
            name="uq_claim_extraction_passage",
        ),
        UniqueConstraint("id", "document_id", name="uq_claim_extraction_document"),
        CheckConstraint("clean_text_hash ~ '^[0-9a-f]{64}$'", name="ck_extraction_text_hash"),
        CheckConstraint(
            "passage_start >= 0 AND passage_end > passage_start", name="ck_extraction_range"
        ),
        CheckConstraint(
            "length(passage_text) = passage_end - passage_start", name="ck_extraction_text_range"
        ),
        CheckConstraint("claim_count BETWEEN 0 AND 3", name="ck_extraction_count"),
        CheckConstraint(
            "length(trim(model)) > 0 AND length(trim(prompt_version)) > 0",
            name="ck_extraction_versions",
        ),
        CheckConstraint("jsonb_typeof(usage) = 'object'", name="ck_extraction_usage"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"))
    clean_text_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(100))
    passage_start: Mapped[int]
    passage_end: Mapped[int]
    passage_text: Mapped[str] = mapped_column(Text)
    claim_count: Mapped[int]
    usage: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    request_id: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
