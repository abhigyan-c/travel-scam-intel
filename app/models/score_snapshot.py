from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
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
from app.models.hypothesis import Hypothesis


class HypothesisScoreSnapshot(Base):
    __tablename__ = "hypothesis_score_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "hypothesis_id", "scoring_version", "input_hash", name="uq_score_snapshot_input"
        ),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_score_snapshot_hash"),
        CheckConstraint("confidence_score BETWEEN 0 AND 1", name="ck_snapshot_confidence"),
        CheckConstraint("prevalence_score BETWEEN 0 AND 1", name="ck_snapshot_prevalence"),
        CheckConstraint("recency_score BETWEEN 0 AND 1", name="ck_snapshot_recency"),
        CheckConstraint("length(trim(scoring_version)) > 0", name="ck_snapshot_version"),
        CheckConstraint("length(trim(explanation)) > 0", name="ck_snapshot_explanation"),
        CheckConstraint("jsonb_typeof(components) = 'object'", name="ck_snapshot_components"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    hypothesis_id: Mapped[UUID] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="RESTRICT"), index=True
    )
    confidence_score: Mapped[float] = mapped_column(Float)
    prevalence_score: Mapped[float | None] = mapped_column(Float)
    recency_score: Mapped[float | None] = mapped_column(Float)
    input_hash: Mapped[str | None] = mapped_column(String(64))
    weight_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "evidence_weight_runs.id", ondelete="RESTRICT", name="fk_score_snapshot_weight_run"
        ),
        index=True,
    )
    scoring_version: Mapped[str] = mapped_column(String(100))
    explanation: Mapped[str] = mapped_column(Text)
    components: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    hypothesis: Mapped[Hypothesis] = relationship(lazy="raise")
