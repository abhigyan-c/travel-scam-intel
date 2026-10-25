from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class HypothesisResolution(Base):
    __tablename__ = "hypothesis_resolutions"
    __table_args__ = (
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_resolution_input_hash"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_resolution_confidence"),
        CheckConstraint(
            "decision IS NULL OR decision IN ('SAME','RELATED','SUBTYPE','CONTRADICTS','NEW')",
            name="ck_resolution_decision",
        ),
        CheckConstraint(
            "(decision IS NULL AND target_id IS NULL) OR "
            "(decision IS NOT NULL AND decision = 'NEW' AND target_id IS NULL) OR "
            "(decision IS NOT NULL AND decision IN ('SAME','RELATED','SUBTYPE','CONTRADICTS') "
            "AND target_id IS NOT NULL)",
            name="ck_resolution_target",
        ),
        CheckConstraint("jsonb_typeof(details) = 'object'", name="ck_resolution_details"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    claim_id: Mapped[UUID] = mapped_column(ForeignKey("claims.id", ondelete="RESTRICT"), index=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    decision: Mapped[str | None] = mapped_column(String(20))
    target_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="RESTRICT"),
        index=True,
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    needs_review: Mapped[bool] = mapped_column(Boolean, index=True)
    error: Mapped[str | None] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index(
    "uq_successful_hypothesis_resolution",
    HypothesisResolution.claim_id,
    HypothesisResolution.input_hash,
    unique=True,
    postgresql_where=HypothesisResolution.error.is_(None),
)
