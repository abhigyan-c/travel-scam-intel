from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, Enum, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class HypothesisStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    DISPUTED = "disputed"
    INACTIVE = "inactive"


class Hypothesis(Base):
    __tablename__ = "hypotheses"
    __table_args__ = (
        CheckConstraint(
            "merged_into_id IS NULL OR (merged_into_id <> id AND status = 'inactive')",
            name="ck_hypothesis_merge_state",
        ),
        CheckConstraint("length(trim(title)) > 0", name="ck_hypothesis_title"),
        CheckConstraint(
            "length(trim(canonical_description)) > 0", name="ck_hypothesis_description"
        ),
        CheckConstraint("length(trim(category)) > 0", name="ck_hypothesis_category"),
        CheckConstraint("confidence_score BETWEEN 0 AND 1", name="ck_hypothesis_confidence"),
        CheckConstraint("prevalence_score BETWEEN 0 AND 1", name="ck_hypothesis_prevalence"),
        CheckConstraint("recency_score BETWEEN 0 AND 1", name="ck_hypothesis_recency"),
        CheckConstraint(
            "(first_seen IS NULL AND last_seen IS NULL) OR "
            "(first_seen IS NOT NULL AND last_seen IS NOT NULL AND first_seen <= last_seen)",
            name="ck_hypothesis_observation_range",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    primary_location_id: Mapped[UUID] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(Text)
    canonical_description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(100))
    merged_into_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="RESTRICT"),
        index=True,
    )
    status: Mapped[HypothesisStatus] = mapped_column(
        Enum(
            HypothesisStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            name="hypothesis_status",
        ),
        default=HypothesisStatus.CANDIDATE,
        server_default=HypothesisStatus.CANDIDATE.value,
    )
    confidence_score: Mapped[float | None] = mapped_column(Float)
    prevalence_score: Mapped[float | None] = mapped_column(Float)
    recency_score: Mapped[float | None] = mapped_column(Float)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384))
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    embedding_revision: Mapped[str | None] = mapped_column(String(200))
    embedding_input_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
