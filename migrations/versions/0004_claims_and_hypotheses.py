"""claims and hypotheses"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hypotheses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("primary_location_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("canonical_description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "candidate",
                "active",
                "disputed",
                "inactive",
                name="hypothesis_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="candidate",
            nullable=False,
        ),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("prevalence_score", sa.Float(), nullable=True),
        sa.Column("recency_score", sa.Float(), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(first_seen IS NULL AND last_seen IS NULL) OR "
            "(first_seen IS NOT NULL AND last_seen IS NOT NULL AND first_seen <= last_seen)",
            name="ck_hypothesis_observation_range",
        ),
        sa.CheckConstraint("confidence_score BETWEEN 0 AND 1", name="ck_hypothesis_confidence"),
        sa.CheckConstraint(
            "length(trim(canonical_description)) > 0", name="ck_hypothesis_description"
        ),
        sa.CheckConstraint("length(trim(category)) > 0", name="ck_hypothesis_category"),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_hypothesis_title"),
        sa.CheckConstraint("prevalence_score BETWEEN 0 AND 1", name="ck_hypothesis_prevalence"),
        sa.CheckConstraint("recency_score BETWEEN 0 AND 1", name="ck_hypothesis_recency"),
        sa.ForeignKeyConstraint(["primary_location_id"], ["locations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_hypotheses_primary_location_id"),
        "hypotheses",
        ["primary_location_id"],
        unique=False,
    )
    op.create_table(
        "claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=True),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("normalized_claim", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(length=100), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extraction_confidence", sa.Float(), nullable=True),
        sa.Column(
            "structured_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column(
            "processing_status",
            sa.Enum(
                "pending",
                "processed",
                "failed",
                name="claim_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("jsonb_typeof(structured_data) = 'object'", name="ck_claim_data_object"),
        sa.CheckConstraint(
            "extraction_confidence BETWEEN 0 AND 1", name="ck_claim_extraction_confidence"
        ),
        sa.CheckConstraint("length(trim(claim_text)) > 0", name="ck_claim_text"),
        sa.CheckConstraint("length(trim(claim_type)) > 0", name="ck_claim_type"),
        sa.CheckConstraint("length(trim(normalized_claim)) > 0", name="ck_claim_normalized"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_claims_document_id"), "claims", ["document_id"], unique=False)
    op.create_index(op.f("ix_claims_location_id"), "claims", ["location_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_claims_location_id"), table_name="claims")
    op.drop_index(op.f("ix_claims_document_id"), table_name="claims")
    op.drop_table("claims")
    op.drop_index(op.f("ix_hypotheses_primary_location_id"), table_name="hypotheses")
    op.drop_table("hypotheses")
