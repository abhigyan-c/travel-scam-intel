from datetime import datetime
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
from app.models.enums import ProcessingStatus
from app.models.source import Source

DocumentStatus = ProcessingStatus


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("source_id", "content_hash", name="uq_document_version"),
        CheckConstraint("length(trim(raw_object_path)) > 0", name="ck_document_raw_path"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_document_content_hash"),
        CheckConstraint(
            "clean_text_hash IS NULL OR clean_text_hash ~ '^[0-9a-f]{64}$'",
            name="ck_document_clean_text_hash",
        ),
        CheckConstraint("url IS NULL OR length(trim(url)) > 0", name="ck_document_url"),
        CheckConstraint(
            "language IS NULL OR length(trim(language)) > 0", name="ck_document_language"
        ),
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="ck_document_metadata_object"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), index=True
    )
    url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    raw_object_path: Mapped[str] = mapped_column(Text)
    clean_text: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(35))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    clean_text_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    document_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    processing_status: Mapped[DocumentStatus] = mapped_column(
        Enum(
            DocumentStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            name="document_status",
        ),
        default=DocumentStatus.PENDING,
        server_default=DocumentStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    source: Mapped[Source] = relationship(lazy="raise")
