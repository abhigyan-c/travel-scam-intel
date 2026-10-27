"""versioned evidence independence scores"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "independence_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("hypothesis_id", sa.Uuid(), nullable=False),
        sa.Column("duplicate_run_id", sa.Uuid(), nullable=False),
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
        sa.CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_independence_input_hash"),
        sa.CheckConstraint("jsonb_typeof(scores) = 'object'", name="ck_independence_scores"),
        sa.CheckConstraint("jsonb_typeof(snapshot) = 'object'", name="ck_independence_snapshot"),
        sa.ForeignKeyConstraint(
            ["duplicate_run_id"], ["duplicate_detection_runs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["hypothesis_id"], ["hypotheses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hypothesis_id", "version", "input_hash", name="uq_independence_run"),
    )
    op.create_index(
        op.f("ix_independence_runs_hypothesis_id"),
        "independence_runs",
        ["hypothesis_id"],
        unique=False,
    )
    op.add_column("evidence", sa.Column("independence_run_id", sa.Uuid(), nullable=True))
    op.add_column(
        "evidence",
        sa.Column(
            "independence_components",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_index(
        op.f("ix_evidence_independence_run_id"), "evidence", ["independence_run_id"], unique=False
    )
    op.create_foreign_key(
        "fk_evidence_independence_run",
        "evidence",
        "independence_runs",
        ["independence_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_evidence_independence_components",
        "evidence",
        "jsonb_typeof(independence_components) = 'object'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_evidence_independence_components", "evidence", type_="check")
    op.drop_constraint("fk_evidence_independence_run", "evidence", type_="foreignkey")
    op.drop_index(op.f("ix_evidence_independence_run_id"), table_name="evidence")
    op.drop_column("evidence", "independence_components")
    op.drop_column("evidence", "independence_run_id")
    op.drop_index(op.f("ix_independence_runs_hypothesis_id"), table_name="independence_runs")
    op.drop_table("independence_runs")
