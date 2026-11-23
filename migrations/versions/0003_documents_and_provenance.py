"""documents and provenance"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("raw_object_path", sa.Text(), nullable=False),
        sa.Column("clean_text", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=35), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "retrieved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "processing_status",
            sa.Enum(
                "pending",
                "processed",
                "failed",
                name="document_status",
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
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_document_content_hash"),
        sa.CheckConstraint("jsonb_typeof(metadata) = 'object'", name="ck_document_metadata_object"),
        sa.CheckConstraint(
            "language IS NULL OR length(trim(language)) > 0", name="ck_document_language"
        ),
        sa.CheckConstraint("length(trim(raw_object_path)) > 0", name="ck_document_raw_path"),
        sa.CheckConstraint("url IS NULL OR length(trim(url)) > 0", name="ck_document_url"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "content_hash", name="uq_document_version"),
    )
    op.create_index(op.f("ix_documents_source_id"), "documents", ["source_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_documents_source_id"), table_name="documents")
    op.drop_table("documents")
