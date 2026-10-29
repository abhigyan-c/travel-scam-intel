"""anonymous report intake and processing"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("job_kind", "research_jobs", type_="check")
    op.create_check_constraint(
        "job_kind",
        "research_jobs",
        "kind IN ('ingest','process','research','location_research','report')",
    )
    op.add_column(
        "user_reports", sa.Column("submission_key_hash", sa.String(length=64), nullable=True)
    )
    op.add_column("user_reports", sa.Column("submission_hash", sa.String(length=64), nullable=True))
    op.add_column("user_reports", sa.Column("processing_job_id", sa.Uuid(), nullable=True))
    op.add_column(
        "user_reports",
        sa.Column(
            "processing_status", sa.String(length=20), server_default="queued", nullable=False
        ),
    )
    op.add_column(
        "user_reports",
        sa.Column(
            "processing_details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_index(
        op.f("ix_user_reports_processing_job_id"),
        "user_reports",
        ["processing_job_id"],
        unique=False,
    )
    op.create_unique_constraint("uq_report_submission_key", "user_reports", ["submission_key_hash"])
    op.create_foreign_key(
        "fk_report_processing_job",
        "user_reports",
        "research_jobs",
        ["processing_job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_report_submission_hashes",
        "user_reports",
        "submission_key_hash ~ '^[0-9a-f]{64}$' AND submission_hash ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_report_submission_pair",
        "user_reports",
        "(submission_key_hash IS NULL) = (submission_hash IS NULL)",
    )
    op.create_check_constraint(
        "ck_report_processing_status",
        "user_reports",
        "processing_status IN ('queued','processed','partial','needs_review','failed')",
    )
    op.create_check_constraint(
        "ck_report_processing_details",
        "user_reports",
        "jsonb_typeof(processing_details) = 'object'",
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM user_reports WHERE submission_key_hash IS NOT NULL) "
            "OR EXISTS (SELECT 1 FROM research_jobs WHERE kind = 'report')"
        )
    ):
        raise RuntimeError(
            "Preserve report submissions and jobs before downgrading their intake schema"
        )
    for name in (
        "ck_report_submission_hashes",
        "ck_report_submission_pair",
        "ck_report_processing_status",
        "ck_report_processing_details",
    ):
        op.drop_constraint(name, "user_reports", type_="check")
    op.drop_constraint("fk_report_processing_job", "user_reports", type_="foreignkey")
    op.drop_constraint("uq_report_submission_key", "user_reports", type_="unique")
    op.drop_index(op.f("ix_user_reports_processing_job_id"), table_name="user_reports")
    op.drop_column("user_reports", "processing_details")
    op.drop_column("user_reports", "processing_status")
    op.drop_column("user_reports", "processing_job_id")
    op.drop_column("user_reports", "submission_hash")
    op.drop_column("user_reports", "submission_key_hash")
    op.drop_constraint("job_kind", "research_jobs", type_="check")
    op.create_check_constraint(
        "job_kind", "research_jobs", "kind IN ('ingest','process','research','location_research')"
    )
