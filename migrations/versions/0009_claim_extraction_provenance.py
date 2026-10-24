"""claim extraction provenance"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "claim_extractions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("clean_text_hash", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("passage_start", sa.Integer(), nullable=False),
        sa.Column("passage_end", sa.Integer(), nullable=False),
        sa.Column("passage_text", sa.Text(), nullable=False),
        sa.Column("claim_count", sa.Integer(), nullable=False),
        sa.Column(
            "usage",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("clean_text_hash ~ '^[0-9a-f]{64}$'", name="ck_extraction_text_hash"),
        sa.CheckConstraint("jsonb_typeof(usage) = 'object'", name="ck_extraction_usage"),
        sa.CheckConstraint("claim_count BETWEEN 0 AND 3", name="ck_extraction_count"),
        sa.CheckConstraint(
            "length(passage_text) = passage_end - passage_start", name="ck_extraction_text_range"
        ),
        sa.CheckConstraint(
            "length(trim(model)) > 0 AND length(trim(prompt_version)) > 0",
            name="ck_extraction_versions",
        ),
        sa.CheckConstraint(
            "passage_start >= 0 AND passage_end > passage_start", name="ck_extraction_range"
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "clean_text_hash",
            "model",
            "prompt_version",
            "passage_start",
            "passage_end",
            name="uq_claim_extraction_passage",
        ),
        sa.UniqueConstraint("id", "document_id", name="uq_claim_extraction_document"),
    )
    op.add_column("claims", sa.Column("extraction_id", sa.Uuid(), nullable=True))
    op.add_column("claims", sa.Column("claim_index", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_claim_extraction_index",
        "claims",
        "(extraction_id IS NULL AND claim_index IS NULL) OR "
        "(extraction_id IS NOT NULL AND claim_index IS NOT NULL AND claim_index >= 0)",
    )
    op.create_unique_constraint(
        "uq_claim_extraction_index", "claims", ["extraction_id", "claim_index"]
    )
    op.create_foreign_key(
        "fk_claim_extraction_document",
        "claims",
        "claim_extractions",
        ["extraction_id", "document_id"],
        ["id", "document_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("ck_claim_extraction_index", "claims", type_="check")
    op.drop_constraint("fk_claim_extraction_document", "claims", type_="foreignkey")
    op.drop_constraint("uq_claim_extraction_index", "claims", type_="unique")
    op.drop_column("claims", "claim_index")
    op.drop_column("claims", "extraction_id")
    op.drop_table("claim_extractions")
