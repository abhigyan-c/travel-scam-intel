from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ResearchRun(Base):
    __tablename__ = "research_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','researching','completed','partial','failed')",
            name="ck_research_run_status",
        ),
        CheckConstraint("coverage IN ('complete','partial','unknown')", name="ck_run_coverage"),
        CheckConstraint("jsonb_typeof(pattern_ids) = 'array'", name="ck_run_patterns"),
        CheckConstraint("jsonb_typeof(observations) = 'object'", name="ck_run_observations"),
        CheckConstraint("jsonb_typeof(stop_reasons) = 'array'", name="ck_run_reasons"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    location_id: Mapped[UUID] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="queued")
    coverage: Mapped[str] = mapped_column(String(20), default="unknown")
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_result_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pattern_ids: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    observations: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    stop_reasons: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
