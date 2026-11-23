from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class DuplicateDetectionRun(Base):
    __tablename__ = "duplicate_detection_runs"
    __table_args__ = (
        UniqueConstraint("hypothesis_id", "version", "input_hash", name="uq_duplicate_run"),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_duplicate_run_hash"),
        CheckConstraint("jsonb_typeof(snapshot) = 'object'", name="ck_duplicate_run_snapshot"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    hypothesis_id: Mapped[UUID] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[str] = mapped_column(String(100))
    input_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DuplicateAssessment(Base):
    __tablename__ = "duplicate_assessments"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "left_claim_id", "right_claim_id", name="uq_duplicate_assessment"
        ),
        CheckConstraint("left_claim_id < right_claim_id", name="ck_duplicate_pair_order"),
        CheckConstraint(
            "classification IN ('duplicate','suspected','no_match')",
            name="ck_duplicate_classification",
        ),
        CheckConstraint("jsonb_typeof(signals) = 'object'", name="ck_duplicate_signals"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("duplicate_detection_runs.id", ondelete="RESTRICT"), index=True
    )
    left_claim_id: Mapped[UUID] = mapped_column(ForeignKey("claims.id", ondelete="RESTRICT"))
    right_claim_id: Mapped[UUID] = mapped_column(ForeignKey("claims.id", ondelete="RESTRICT"))
    classification: Mapped[str] = mapped_column(String(20))
    signals: Mapped[dict] = mapped_column(JSONB)
