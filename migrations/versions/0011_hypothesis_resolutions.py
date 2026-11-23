"""Durable hypothesis decisions and review records; graph changes are separate."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "hypothesis_resolutions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "claim_id", sa.Uuid(), sa.ForeignKey("claims.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("decision", sa.String(20), nullable=True),
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("hypotheses.id", ondelete="RESTRICT")),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String(100), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_resolution_input_hash"),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_resolution_confidence"),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('SAME','RELATED','SUBTYPE','CONTRADICTS','NEW')",
            name="ck_resolution_decision",
        ),
        sa.CheckConstraint(
            "(decision IS NULL AND target_id IS NULL) OR "
            "(decision IS NOT NULL AND decision = 'NEW' AND target_id IS NULL) OR "
            "(decision IS NOT NULL AND decision IN ('SAME','RELATED','SUBTYPE','CONTRADICTS') "
            "AND target_id IS NOT NULL)",
            name="ck_resolution_target",
        ),
        sa.CheckConstraint("jsonb_typeof(details) = 'object'", name="ck_resolution_details"),
    )
    for name in ("claim_id", "target_id", "needs_review"):
        op.create_index(f"ix_hypothesis_resolutions_{name}", "hypothesis_resolutions", [name])
    op.create_index(
        "uq_successful_hypothesis_resolution",
        "hypothesis_resolutions",
        ["claim_id", "input_hash"],
        unique=True,
        postgresql_where=sa.text("error IS NULL"),
    )


def downgrade():
    op.drop_table("hypothesis_resolutions")
