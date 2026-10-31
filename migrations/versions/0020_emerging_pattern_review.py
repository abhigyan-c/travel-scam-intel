"""emerging pattern assessments and review queue"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "emerging_assessments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("hypothesis_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("material_hash", sa.String(length=64), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("candidate", sa.Boolean(), nullable=True),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_emerging_hash"),
        sa.CheckConstraint("jsonb_typeof(signals) = 'object'", name="ck_emerging_signals"),
        sa.CheckConstraint("material_hash ~ '^[0-9a-f]{64}$'", name="ck_emerging_material"),
        sa.ForeignKeyConstraint(["hypothesis_id"], ["hypotheses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hypothesis_id", "version", "input_hash", name="uq_emerging_input"),
        sa.UniqueConstraint("id", "hypothesis_id", name="uq_emerging_identity"),
    )
    op.create_index(
        op.f("ix_emerging_assessments_hypothesis_id"),
        "emerging_assessments",
        ["hypothesis_id"],
        unique=False,
    )
    op.create_table(
        "emerging_review_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("hypothesis_id", sa.Uuid(), nullable=False),
        sa.Column("assessment_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('acknowledged','dismissed')", name="ck_emerging_decision_action"
        ),
        sa.CheckConstraint("revision > 0", name="ck_emerging_decision_revision"),
        sa.CheckConstraint(
            "length(trim(reason)) BETWEEN 1 AND 500", name="ck_emerging_decision_reason"
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id", "hypothesis_id"],
            ["emerging_assessments.id", "emerging_assessments.hypothesis_id"],
            name="fk_emerging_decision_assessment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["hypothesis_id"], ["hypotheses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hypothesis_id", "revision", name="uq_emerging_decision_revision"),
    )
    op.create_index(
        op.f("ix_emerging_review_decisions_hypothesis_id"),
        "emerging_review_decisions",
        ["hypothesis_id"],
        unique=False,
    )
    op.create_table(
        "emerging_reviews",
        sa.Column("hypothesis_id", sa.Uuid(), nullable=False),
        sa.Column("assessment_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending','acknowledged','dismissed','withdrawn','needs_assessment')",
            name="ck_emerging_review_status",
        ),
        sa.CheckConstraint("revision > 0", name="ck_emerging_review_revision"),
        sa.ForeignKeyConstraint(
            ["assessment_id", "hypothesis_id"],
            ["emerging_assessments.id", "emerging_assessments.hypothesis_id"],
            name="fk_emerging_review_assessment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["hypothesis_id"], ["hypotheses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("hypothesis_id"),
    )
    op.create_index("ix_emerging_review_queue", "emerging_reviews", ["status", "updated_at"])


def downgrade() -> None:
    op.drop_table("emerging_reviews")
    op.drop_index(
        op.f("ix_emerging_review_decisions_hypothesis_id"), table_name="emerging_review_decisions"
    )
    op.drop_table("emerging_review_decisions")
    op.drop_index(op.f("ix_emerging_assessments_hypothesis_id"), table_name="emerging_assessments")
    op.drop_table("emerging_assessments")
