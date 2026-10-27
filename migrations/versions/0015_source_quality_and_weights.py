"""versioned source quality evidence weights and report moderation"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidence_weight_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("hypothesis_id", sa.Uuid(), nullable=False),
        sa.Column("independence_run_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("scores", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_weight_run_hash"),
        sa.CheckConstraint("jsonb_typeof(scores) = 'object'", name="ck_weight_run_scores"),
        sa.CheckConstraint("jsonb_typeof(snapshot) = 'object'", name="ck_weight_run_snapshot"),
        sa.ForeignKeyConstraint(["hypothesis_id"], ["hypotheses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["independence_run_id"], ["independence_runs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "hypothesis_id", "version", "input_hash", name="uq_evidence_weight_run"
        ),
    )
    op.create_index(
        op.f("ix_evidence_weight_runs_hypothesis_id"),
        "evidence_weight_runs",
        ["hypothesis_id"],
        unique=False,
    )
    op.add_column("evidence", sa.Column("weight_run_id", sa.Uuid(), nullable=True))
    op.add_column(
        "evidence",
        sa.Column(
            "weight_components",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_index(op.f("ix_evidence_weight_run_id"), "evidence", ["weight_run_id"], unique=False)
    op.create_foreign_key(
        "fk_evidence_weight_run",
        "evidence",
        "evidence_weight_runs",
        ["weight_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "user_reports",
        sa.Column(
            "moderation_status", sa.String(length=20), server_default="pending", nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_user_report_moderation_status",
        "user_reports",
        "moderation_status IN ('pending','processed','needs_review','accepted','rejected')",
    )
    op.create_check_constraint(
        "ck_evidence_weight_components", "evidence", "jsonb_typeof(weight_components) = 'object'"
    )


def downgrade() -> None:
    op.drop_constraint("ck_evidence_weight_components", "evidence", type_="check")
    op.drop_constraint("ck_user_report_moderation_status", "user_reports", type_="check")
    op.drop_column("user_reports", "moderation_status")
    op.drop_constraint("fk_evidence_weight_run", "evidence", type_="foreignkey")
    op.drop_index(op.f("ix_evidence_weight_run_id"), table_name="evidence")
    op.drop_column("evidence", "weight_components")
    op.drop_column("evidence", "weight_run_id")
    op.drop_index(op.f("ix_evidence_weight_runs_hypothesis_id"), table_name="evidence_weight_runs")
    op.drop_table("evidence_weight_runs")
