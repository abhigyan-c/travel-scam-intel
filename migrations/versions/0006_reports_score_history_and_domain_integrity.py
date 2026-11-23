"""reports score history and domain integrity"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "duplicate_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "hypothesis_score_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("hypothesis_id", sa.Uuid(), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("prevalence_score", sa.Float(), nullable=False),
        sa.Column("recency_score", sa.Float(), nullable=False),
        sa.Column("scoring_version", sa.String(length=100), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column(
            "components",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("jsonb_typeof(components) = 'object'", name="ck_snapshot_components"),
        sa.CheckConstraint("confidence_score BETWEEN 0 AND 1", name="ck_snapshot_confidence"),
        sa.CheckConstraint("length(trim(explanation)) > 0", name="ck_snapshot_explanation"),
        sa.CheckConstraint("length(trim(scoring_version)) > 0", name="ck_snapshot_version"),
        sa.CheckConstraint("prevalence_score BETWEEN 0 AND 1", name="ck_snapshot_prevalence"),
        sa.CheckConstraint("recency_score BETWEEN 0 AND 1", name="ck_snapshot_recency"),
        sa.ForeignKeyConstraint(["hypothesis_id"], ["hypotheses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_hypothesis_score_snapshots_hypothesis_id"),
        "hypothesis_score_snapshots",
        ["hypothesis_id"],
        unique=False,
    )
    op.create_table(
        "user_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=True),
        sa.Column("report_text", sa.Text(), nullable=False),
        sa.Column(
            "provenance_class",
            sa.Enum(
                "FIRST_HAND",
                "WITNESSED",
                "SECOND_HAND",
                name="report_provenance",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(trim(report_text)) > 0", name="ck_user_report_text"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id"),
    )
    op.create_index(
        op.f("ix_user_reports_location_id"), "user_reports", ["location_id"], unique=False
    )
    op.create_foreign_key(
        "fk_evidence_duplicate_group",
        "evidence",
        "duplicate_groups",
        ["duplicate_group_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_evidence_duplicate_group", "evidence", type_="foreignkey")
    op.drop_index(op.f("ix_user_reports_location_id"), table_name="user_reports")
    op.drop_table("user_reports")
    op.drop_index(
        op.f("ix_hypothesis_score_snapshots_hypothesis_id"), table_name="hypothesis_score_snapshots"
    )
    op.drop_table("hypothesis_score_snapshots")
    op.drop_table("duplicate_groups")
