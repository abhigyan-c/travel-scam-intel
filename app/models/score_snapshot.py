from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.hypothesis import Hypothesis


class HypothesisScoreSnapshot(Base):
    __tablename__ = "hypothesis_score_snapshots"
    __table_args__ = (
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
    prevalence_score: Mapped[float] = mapped_column(Float)
    recency_score: Mapped[float] = mapped_column(Float)
    scoring_version: Mapped[str] = mapped_column(String(100))
    explanation: Mapped[str] = mapped_column(Text)
    components: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    hypothesis: Mapped[Hypothesis] = relationship(lazy="raise")
