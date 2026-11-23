"""Private operator audit and bounded location priority override."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023"
down_revision = "0022"
branch_labels = depends_on = None


def upgrade():
    op.drop_constraint("ck_lifecycle_operation", "hypothesis_lifecycle_events", type_="check")
    op.create_check_constraint(
        "ck_lifecycle_operation",
        "hypothesis_lifecycle_events",
        "operation IN ('apply','update','merge','split','link','move')",
    )
    op.create_table(
        "admin_actions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="ck_admin_key_hash"),
        sa.CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_admin_input_hash"),
        sa.CheckConstraint("length(trim(reason)) BETWEEN 1 AND 2000", name="ck_admin_reason"),
        sa.CheckConstraint("jsonb_typeof(result)='object'", name="ck_admin_result"),
    )
    op.create_index("ix_admin_actions_subject_id", "admin_actions", ["subject_id"])
    op.add_column(
        "location_research_states",
        sa.Column("priority_override", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_location_priority", "location_research_states", "priority_override BETWEEN -100 AND 100"
    )


def downgrade():
    # Preserve existing lifecycle history, including move events, when rolling back admin UI.
    # The widened constraint is compatible with older readers and cannot safely narrow.
    op.drop_constraint("ck_location_priority", "location_research_states", type_="check")
    op.drop_column("location_research_states", "priority_override")
    op.drop_table("admin_actions")
