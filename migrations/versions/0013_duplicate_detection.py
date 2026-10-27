"""duplicate detection audit and group fingerprints"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "duplicate_detection_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("hypothesis_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_duplicate_run_hash"),
        sa.CheckConstraint("jsonb_typeof(snapshot) = 'object'", name="ck_duplicate_run_snapshot"),
        sa.ForeignKeyConstraint(["hypothesis_id"], ["hypotheses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hypothesis_id", "version", "input_hash", name="uq_duplicate_run"),
    )
    op.create_index(
        op.f("ix_duplicate_detection_runs_hypothesis_id"),
        "duplicate_detection_runs",
        ["hypothesis_id"],
        unique=False,
    )
    op.create_table(
        "duplicate_assessments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("left_claim_id", sa.Uuid(), nullable=False),
        sa.Column("right_claim_id", sa.Uuid(), nullable=False),
        sa.Column("classification", sa.String(length=20), nullable=False),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "classification IN ('duplicate','suspected','no_match')",
            name="ck_duplicate_classification",
        ),
        sa.CheckConstraint("jsonb_typeof(signals) = 'object'", name="ck_duplicate_signals"),
        sa.CheckConstraint("left_claim_id < right_claim_id", name="ck_duplicate_pair_order"),
        sa.ForeignKeyConstraint(["left_claim_id"], ["claims.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["right_claim_id"], ["claims.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["duplicate_detection_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "left_claim_id", "right_claim_id", name="uq_duplicate_assessment"
        ),
    )
    op.create_index(
        op.f("ix_duplicate_assessments_run_id"), "duplicate_assessments", ["run_id"], unique=False
    )
    op.add_column("duplicate_groups", sa.Column("fingerprint", sa.String(length=64), nullable=True))
    op.add_column(
        "duplicate_groups",
        sa.Column(
            "detection_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_duplicate_group_fingerprint", "duplicate_groups", ["fingerprint"]
    )
    op.create_check_constraint(
        "ck_duplicate_group_fingerprint",
        "duplicate_groups",
        "fingerprint IS NULL OR fingerprint ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_duplicate_group_metadata",
        "duplicate_groups",
        "jsonb_typeof(detection_metadata) = 'object'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_duplicate_group_metadata", "duplicate_groups", type_="check")
    op.drop_constraint("ck_duplicate_group_fingerprint", "duplicate_groups", type_="check")
    op.drop_constraint("uq_duplicate_group_fingerprint", "duplicate_groups", type_="unique")
    op.drop_column("duplicate_groups", "detection_metadata")
    op.drop_column("duplicate_groups", "fingerprint")
    op.drop_index(op.f("ix_duplicate_assessments_run_id"), table_name="duplicate_assessments")
    op.drop_table("duplicate_assessments")
    op.drop_index(
        op.f("ix_duplicate_detection_runs_hypothesis_id"), table_name="duplicate_detection_runs"
    )
    op.drop_table("duplicate_detection_runs")
