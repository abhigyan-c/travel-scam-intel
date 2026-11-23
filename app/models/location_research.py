from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class LocationResearchState(Base):
    __tablename__ = "location_research_states"
    __table_args__ = (
        CheckConstraint(
            "status IN ('never_researched','researching','ready','stale','failed')",
            name="ck_location_research_status",
        ),
        CheckConstraint(
            "(run_token IS NULL) = (lease_expires_at IS NULL)", name="ck_location_research_lease"
        ),
        CheckConstraint(
            "(status = 'researching') = (run_token IS NOT NULL)",
            name="ck_location_research_running",
        ),
        CheckConstraint("jsonb_typeof(progress) = 'object'", name="ck_location_research_progress"),
        CheckConstraint("priority_override BETWEEN -100 AND 100", name="ck_location_priority"),
    )
    location_id: Mapped[UUID] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT"), primary_key=True
    )
    priority_override: Mapped[int] = mapped_column(default=0, server_default="0")
    status: Mapped[str] = mapped_column(
        String(30), default="never_researched", server_default="never_researched"
    )
    current_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("research_runs.id", ondelete="RESTRICT", name="fk_location_current_run"),
        index=True,
    )
    active_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="RESTRICT"), index=True
    )
    run_token: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    initial_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fresh_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    progress: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
