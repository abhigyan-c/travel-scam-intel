from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class EvidenceWeightRun(Base):
    __tablename__ = "evidence_weight_runs"
    __table_args__ = (
        UniqueConstraint("hypothesis_id", "version", "input_hash", name="uq_evidence_weight_run"),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_weight_run_hash"),
        CheckConstraint("jsonb_typeof(snapshot) = 'object'", name="ck_weight_run_snapshot"),
        CheckConstraint("jsonb_typeof(scores) = 'object'", name="ck_weight_run_scores"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    hypothesis_id: Mapped[UUID] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="RESTRICT"), index=True
    )
    independence_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("independence_runs.id", ondelete="RESTRICT")
    )
    version: Mapped[str] = mapped_column(String(100))
    input_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    scores: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
