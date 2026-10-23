"""durable research jobs"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "ingest",
                "process",
                "research",
                name="job_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "retry",
                "succeeded",
                "failed",
                name="job_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("run_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'running') = (run_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_job_lease",
        ),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_job_payload"),
        sa.CheckConstraint("jsonb_typeof(result) = 'object'", name="ck_job_result"),
        sa.CheckConstraint("attempts BETWEEN 0 AND 3", name="ck_job_attempts"),
        sa.CheckConstraint("length(trim(idempotency_key)) > 0", name="ck_job_key"),
        sa.CheckConstraint(
            "(run_token IS NULL) = (lease_expires_at IS NULL)", name="ck_job_lease_pair"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        op.f("ix_research_jobs_lease_expires_at"),
        "research_jobs",
        ["lease_expires_at"],
        unique=False,
    )
    op.create_index(op.f("ix_research_jobs_status"), "research_jobs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_research_jobs_status"), table_name="research_jobs")
    op.drop_index(op.f("ix_research_jobs_lease_expires_at"), table_name="research_jobs")
    op.drop_table("research_jobs")
