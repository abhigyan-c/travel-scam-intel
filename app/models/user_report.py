from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.document import Document


class ReportProvenance(StrEnum):
    FIRST_HAND = "FIRST_HAND"
    WITNESSED = "WITNESSED"
    SECOND_HAND = "SECOND_HAND"


class UserReport(Base):
    __tablename__ = "user_reports"
    __table_args__ = (
        UniqueConstraint("submission_key_hash", name="uq_report_submission_key"),
        CheckConstraint(
            "submission_key_hash ~ '^[0-9a-f]{64}$' AND submission_hash ~ '^[0-9a-f]{64}$'",
            name="ck_report_submission_hashes",
        ),
        CheckConstraint(
            "(submission_key_hash IS NULL) = (submission_hash IS NULL)",
            name="ck_report_submission_pair",
        ),
        CheckConstraint(
            "processing_status IN ('queued','processed','partial','needs_review','failed')",
            name="ck_report_processing_status",
        ),
        CheckConstraint(
            "jsonb_typeof(processing_details) = 'object'", name="ck_report_processing_details"
        ),
        CheckConstraint("length(trim(report_text)) > 0", name="ck_user_report_text"),
        CheckConstraint(
            "moderation_status IN ('pending','processed','needs_review','accepted','rejected')",
            name="ck_user_report_moderation_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"), unique=True
    )
    location_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT"), index=True
    )
    report_text: Mapped[str] = mapped_column(Text)
    submission_key_hash: Mapped[str | None] = mapped_column(String(64))
    submission_hash: Mapped[str | None] = mapped_column(String(64))
    processing_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="RESTRICT", name="fk_report_processing_job"),
        index=True,
    )
    processing_status: Mapped[str] = mapped_column(
        String(20), default="queued", server_default="queued"
    )
    processing_details: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    moderation_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending"
    )
    provenance_class: Mapped[ReportProvenance] = mapped_column(
        Enum(
            ReportProvenance,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            name="report_provenance",
        )
    )
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    document: Mapped[Document] = relationship(lazy="raise")
