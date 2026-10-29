from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class JobKind(StrEnum):
    INGEST = "ingest"
    PROCESS = "process"
    RESEARCH = "research"
    LOCATION_RESEARCH = "location_research"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY = "retry"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ResearchJob(Base):
    __tablename__ = "research_jobs"
    __table_args__ = (
        CheckConstraint(
            "(run_token IS NULL) = (lease_expires_at IS NULL)", name="ck_job_lease_pair"
        ),
        CheckConstraint("length(trim(idempotency_key)) > 0", name="ck_job_key"),
        CheckConstraint("attempts BETWEEN 0 AND 3", name="ck_job_attempts"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_job_payload"),
        CheckConstraint("jsonb_typeof(result) = 'object'", name="ck_job_result"),
        CheckConstraint(
            "(status = 'running') = (run_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_job_lease",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[JobKind] = mapped_column(
        Enum(
            JobKind,
            values_callable=lambda e: [v.value for v in e],
            native_enum=False,
            create_constraint=True,
            name="job_kind",
        )
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(
            JobStatus,
            values_callable=lambda e: [v.value for v in e],
            native_enum=False,
            create_constraint=True,
            name="job_status",
        ),
        default=JobStatus.QUEUED,
        server_default="queued",
        index=True,
    )
    payload: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    run_token: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
