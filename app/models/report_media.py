from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ReportAttachment(Base):
    __tablename__ = "report_attachments"
    __table_args__ = (
        UniqueConstraint("report_id", "upload_key_hash", name="uq_report_attachment_upload"),
        CheckConstraint(
            "status IN ('needs_review','accepted','rejected')", name="ck_attachment_status"
        ),
        CheckConstraint("media_type IN ('image/jpeg','image/png')", name="ck_attachment_type"),
        CheckConstraint(
            "original_size BETWEEN 1 AND 5242880 AND stored_size BETWEEN 1 AND 5242880",
            name="ck_attachment_sizes",
        ),
        CheckConstraint(
            "width > 0 AND height > 0 AND width::bigint * height <= 10000000",
            name="ck_attachment_dimensions",
        ),
        CheckConstraint(
            "upload_key_hash ~ '^[0-9a-f]{64}$' AND original_hash ~ '^[0-9a-f]{64}$' "
            "AND stored_hash ~ '^[0-9a-f]{64}$'",
            name="ck_attachment_hashes",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_reports.id", ondelete="RESTRICT"), index=True
    )
    upload_key_hash: Mapped[str] = mapped_column(String(64))
    original_hash: Mapped[str] = mapped_column(String(64))
    stored_hash: Mapped[str] = mapped_column(String(64))
    original_size: Mapped[int]
    stored_size: Mapped[int]
    width: Mapped[int]
    height: Mapped[int]
    media_type: Mapped[str] = mapped_column(String(30))
    raw_object_path: Mapped[str] = mapped_column(Text)
    private_object_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), default="needs_review", server_default="needs_review"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReportModerationEvent(Base):
    __tablename__ = "report_moderation_events"
    __table_args__ = (
        UniqueConstraint("report_id", "key_hash", name="uq_report_moderation_key"),
        CheckConstraint("revision > 0", name="ck_moderation_event_revision"),
        CheckConstraint(
            "key_hash ~ '^[0-9a-f]{64}$' AND request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_moderation_event_hashes",
        ),
        CheckConstraint(
            "action IN ('attachment_added','processing_completed','accepted',"
            "'rejected','needs_review')",
            name="ck_moderation_event_action",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_reports.id", ondelete="RESTRICT"), index=True
    )
    key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(30))
    actor: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(Text)
    previous_status: Mapped[str] = mapped_column(String(20))
    next_status: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
