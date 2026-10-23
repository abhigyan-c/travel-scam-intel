from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, Enum, Float, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.document import Document


class ClaimStatus(StrEnum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint("length(trim(claim_text)) > 0", name="ck_claim_text"),
        CheckConstraint("length(trim(normalized_claim)) > 0", name="ck_claim_normalized"),
        CheckConstraint("length(trim(claim_type)) > 0", name="ck_claim_type"),
        CheckConstraint(
            "extraction_confidence BETWEEN 0 AND 1", name="ck_claim_extraction_confidence"
        ),
        CheckConstraint("jsonb_typeof(structured_data) = 'object'", name="ck_claim_data_object"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
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
