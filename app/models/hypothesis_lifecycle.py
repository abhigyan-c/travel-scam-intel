from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class HypothesisLifecycleEvent(Base):
    __tablename__ = "hypothesis_lifecycle_events"
    __table_args__ = (
        UniqueConstraint("key", name="uq_lifecycle_key"),
        UniqueConstraint("resolution_id", name="uq_lifecycle_resolution"),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_lifecycle_input_hash"),
        CheckConstraint(
            "operation IN ('apply','update','merge','split','link','move')",
            name="ck_lifecycle_operation",
        ),
        CheckConstraint("length(trim(key)) > 0", name="ck_lifecycle_key"),
        CheckConstraint(
            "length(trim(actor)) > 0 AND length(trim(reason)) > 0", name="ck_lifecycle_review"
        ),
        CheckConstraint("jsonb_typeof(details) = 'object'", name="ck_lifecycle_details"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    key: Mapped[str] = mapped_column(String(200))
    input_hash: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(20))
    resolution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("hypothesis_resolutions.id", ondelete="RESTRICT"),
    )
    source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="RESTRICT"),
        index=True,
    )
    target_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="RESTRICT"),
        index=True,
    )
    actor: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(String(2000))
    details: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
