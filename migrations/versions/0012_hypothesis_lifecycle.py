"""Lifecycle audit and preserved evidence attachment history."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("hypotheses", sa.Column("merged_into_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_hypothesis_merged_into",
        "hypotheses",
        "hypotheses",
        ["merged_into_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_hypotheses_merged_into_id", "hypotheses", ["merged_into_id"])
    op.create_check_constraint(
        "ck_hypothesis_merge_state",
        "hypotheses",
        "merged_into_id IS NULL OR (merged_into_id <> id AND status = 'inactive')",
    )
    op.add_column("evidence", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("evidence", sa.Column("superseded_by_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_evidence_superseded_by",
        "evidence",
        "evidence",
        ["superseded_by_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_evidence_superseded_by_id", "evidence", ["superseded_by_id"])
    op.create_check_constraint(
        "ck_evidence_archive_state",
        "evidence",
        "(archived_at IS NULL AND superseded_by_id IS NULL) OR "
        "(archived_at IS NOT NULL AND superseded_by_id IS NOT NULL AND superseded_by_id <> id)",
    )
    op.create_table(
        "hypothesis_lifecycle_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column(
            "resolution_id",
            sa.Uuid(),
            sa.ForeignKey("hypothesis_resolutions.id", ondelete="RESTRICT"),
        ),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("hypotheses.id", ondelete="RESTRICT")),
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("hypotheses.id", ondelete="RESTRICT")),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("key", name="uq_lifecycle_key"),
        sa.UniqueConstraint("resolution_id", name="uq_lifecycle_resolution"),
        sa.CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_lifecycle_input_hash"),
        sa.CheckConstraint(
            "operation IN ('apply','update','merge','split','link')", name="ck_lifecycle_operation"
        ),
        sa.CheckConstraint("length(trim(key)) > 0", name="ck_lifecycle_key"),
        sa.CheckConstraint(
            "length(trim(actor)) > 0 AND length(trim(reason)) > 0", name="ck_lifecycle_review"
        ),
        sa.CheckConstraint("jsonb_typeof(details) = 'object'", name="ck_lifecycle_details"),
    )
    for name in ("source_id", "target_id"):
        op.create_index(
            f"ix_hypothesis_lifecycle_events_{name}", "hypothesis_lifecycle_events", [name]
        )


def downgrade():
    op.drop_table("hypothesis_lifecycle_events")
    op.drop_constraint("ck_evidence_archive_state", "evidence", type_="check")
    op.drop_index("ix_evidence_superseded_by_id", table_name="evidence")
    op.drop_constraint("fk_evidence_superseded_by", "evidence", type_="foreignkey")
    op.drop_column("evidence", "superseded_by_id")
    op.drop_column("evidence", "archived_at")
    op.drop_constraint("ck_hypothesis_merge_state", "hypotheses", type_="check")
    op.drop_index("ix_hypotheses_merged_into_id", table_name="hypotheses")
    op.drop_constraint("fk_hypothesis_merged_into", "hypotheses", type_="foreignkey")
    op.drop_column("hypotheses", "merged_into_id")
