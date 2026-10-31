from datetime import date, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class LocationDemandHour(Base):
    __tablename__ = "location_demand_hours"
    __table_args__ = (CheckConstraint("requests > 0", name="ck_demand_requests"),)
    location_id: Mapped[UUID] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT"), primary_key=True
    )
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, index=True)
    requests: Mapped[int] = mapped_column(Integer)


class SchedulerDailyBudget(Base):
    __tablename__ = "scheduler_daily_budgets"
    __table_args__ = (
        CheckConstraint(
            "jobs >= 0 AND queries >= 0 AND fetches >= 0 AND llm_calls >= 0",
            name="ck_scheduler_usage",
        ),
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    jobs: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    queries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    fetches: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    llm_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class SchedulerReservation(Base):
    __tablename__ = "scheduler_reservations"
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="RESTRICT"), primary_key=True
    )
    day: Mapped[date] = mapped_column(
        ForeignKey("scheduler_daily_budgets.day", ondelete="RESTRICT"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SchedulerCycle(Base):
    __tablename__ = "scheduler_cycles"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(summary) = 'object'", name="ck_scheduler_cycle_summary"),
    )
    slot: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    summary: Mapped[dict] = mapped_column(JSONB)


class HypothesisResearchState(Base):
    __tablename__ = "hypothesis_research_states"
    hypothesis_id: Mapped[UUID] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="RESTRICT"), primary_key=True
    )
    active_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="RESTRICT"), index=True
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
