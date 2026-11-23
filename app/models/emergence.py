from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class EmergingAssessment(Base):
    __tablename__ = "emerging_assessments"
    __table_args__ = (
        UniqueConstraint("hypothesis_id", "version", "input_hash", name="uq_emerging_input"),
        UniqueConstraint("id", "hypothesis_id", name="uq_emerging_identity"),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_emerging_hash"),
        CheckConstraint("material_hash ~ '^[0-9a-f]{64}$'", name="ck_emerging_material"),
        CheckConstraint("jsonb_typeof(signals) = 'object'", name="ck_emerging_signals"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    hypothesis_id: Mapped[UUID] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[str] = mapped_column(String(50))
    input_hash: Mapped[str] = mapped_column(String(64))
    material_hash: Mapped[str] = mapped_column(String(64))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    candidate: Mapped[bool | None] = mapped_column(Boolean)
    signals: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmergingReview(Base):
    __tablename__ = "emerging_reviews"
    __table_args__ = (
        Index("ix_emerging_review_queue", "status", "updated_at"),
        ForeignKeyConstraint(
            ["assessment_id", "hypothesis_id"],
            ["emerging_assessments.id", "emerging_assessments.hypothesis_id"],
            ondelete="RESTRICT",
            name="fk_emerging_review_assessment",
        ),
        CheckConstraint("revision > 0", name="ck_emerging_review_revision"),
        CheckConstraint(
            "status IN ('pending','acknowledged','dismissed','withdrawn','needs_assessment')",
            name="ck_emerging_review_status",
        ),
    )
    hypothesis_id: Mapped[UUID] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="RESTRICT"), primary_key=True
    )
    assessment_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EmergingReviewDecision(Base):
    __tablename__ = "emerging_review_decisions"
    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_emerging_decision_revision"),
        UniqueConstraint("hypothesis_id", "revision", name="uq_emerging_decision_revision"),
        ForeignKeyConstraint(
            ["assessment_id", "hypothesis_id"],
            ["emerging_assessments.id", "emerging_assessments.hypothesis_id"],
            ondelete="RESTRICT",
            name="fk_emerging_decision_assessment",
        ),
        CheckConstraint(
            "action IN ('acknowledged','dismissed')", name="ck_emerging_decision_action"
        ),
        CheckConstraint(
            "length(trim(reason)) BETWEEN 1 AND 500", name="ck_emerging_decision_reason"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    hypothesis_id: Mapped[UUID] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="RESTRICT"), index=True
    )
    assessment_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
