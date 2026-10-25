from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
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
from app.models.enums import ProcessingStatus

ClaimStatus = ProcessingStatus


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (
        ForeignKeyConstraint(
            ["extraction_id", "document_id"],
            ["claim_extractions.id", "claim_extractions.document_id"],
            ondelete="RESTRICT",
            name="fk_claim_extraction_document",
        ),
        UniqueConstraint("extraction_id", "claim_index", name="uq_claim_extraction_index"),
        CheckConstraint(
            "(extraction_id IS NULL AND claim_index IS NULL) OR "
            "(extraction_id IS NOT NULL AND claim_index IS NOT NULL AND claim_index >= 0)",
            name="ck_claim_extraction_index",
        ),
        CheckConstraint("length(trim(claim_text)) > 0", name="ck_claim_text"),
        CheckConstraint("length(trim(normalized_claim)) > 0", name="ck_claim_normalized"),
        CheckConstraint("length(trim(claim_type)) > 0", name="ck_claim_type"),
        CheckConstraint(
            "extraction_confidence BETWEEN 0 AND 1", name="ck_claim_extraction_confidence"
        ),
        CheckConstraint("jsonb_typeof(structured_data) = 'object'", name="ck_claim_data_object"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    extraction_id: Mapped[UUID | None]
    claim_index: Mapped[int | None]
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"), index=True
    )
    location_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT"), index=True
    )
    claim_text: Mapped[str] = mapped_column(Text)
    normalized_claim: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(String(100))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    structured_data: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384))
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    embedding_revision: Mapped[str | None] = mapped_column(String(200))
    embedding_input_hash: Mapped[str | None] = mapped_column(String(64))
    processing_status: Mapped[ClaimStatus] = mapped_column(
        Enum(
            ClaimStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            name="claim_status",
        ),
        default=ClaimStatus.PENDING,
        server_default=ClaimStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    document: Mapped[Document] = relationship(lazy="raise")
