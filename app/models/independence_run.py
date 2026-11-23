from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class IndependenceRun(Base):
    __tablename__ = "independence_runs"
    __table_args__ = (
        UniqueConstraint("hypothesis_id", "version", "input_hash", name="uq_independence_run"),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_independence_input_hash"),
        CheckConstraint("jsonb_typeof(snapshot) = 'object'", name="ck_independence_snapshot"),
        CheckConstraint("jsonb_typeof(scores) = 'object'", name="ck_independence_scores"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    hypothesis_id: Mapped[UUID] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="RESTRICT"), index=True
    )
    duplicate_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("duplicate_detection_runs.id", ondelete="RESTRICT")
    )
    version: Mapped[str] = mapped_column(String(100))
    input_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    scores: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
