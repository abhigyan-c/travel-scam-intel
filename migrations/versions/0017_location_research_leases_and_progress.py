"""location research leases and progress"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "location_research_states",
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status", sa.String(length=30), server_default="never_researched", nullable=False
        ),
        sa.Column("active_job_id", sa.Uuid(), nullable=True),
        sa.Column("run_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("initial_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fresh_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "progress",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'researching') = (run_token IS NOT NULL)",
            name="ck_location_research_running",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(progress) = 'object'", name="ck_location_research_progress"
        ),
        sa.CheckConstraint(
            "status IN ('never_researched','researching','ready','stale','failed')",
            name="ck_location_research_status",
        ),
        sa.CheckConstraint(
            "(run_token IS NULL) = (lease_expires_at IS NULL)", name="ck_location_research_lease"
        ),
        sa.ForeignKeyConstraint(["active_job_id"], ["research_jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("location_id"),
    )
    op.create_index(
        op.f("ix_location_research_states_active_job_id"),
        "location_research_states",
        ["active_job_id"],
        unique=False,
    )
    op.drop_constraint("job_kind", "research_jobs", type_="check")
    op.alter_column("research_jobs", "kind", type_=sa.String(17), existing_nullable=False)
    op.create_check_constraint(
        "job_kind", "research_jobs", "kind IN ('ingest','process','research','location_research')"
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM research_jobs WHERE kind = 'location_research')")
    ):
        raise RuntimeError("Location research jobs must be explicitly archived before downgrade")
    op.drop_constraint("job_kind", "research_jobs", type_="check")
    op.alter_column("research_jobs", "kind", type_=sa.String(8), existing_nullable=False)
    op.create_check_constraint(
        "job_kind", "research_jobs", "kind IN ('ingest','process','research')"
    )
    op.drop_index(
        op.f("ix_location_research_states_active_job_id"), table_name="location_research_states"
    )
    op.drop_table("location_research_states")
