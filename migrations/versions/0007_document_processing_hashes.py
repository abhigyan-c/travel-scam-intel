"""document processing hashes"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("clean_text_hash", sa.String(length=64), nullable=True))
    op.create_check_constraint(
        "ck_document_clean_text_hash",
        "documents",
        "clean_text_hash IS NULL OR clean_text_hash ~ '^[0-9a-f]{64}$'",
    )
    op.create_index(
        op.f("ix_documents_clean_text_hash"), "documents", ["clean_text_hash"], unique=False
    )
    op.create_index(op.f("ix_documents_content_hash"), "documents", ["content_hash"], unique=False)


def downgrade() -> None:
    op.drop_constraint("ck_document_clean_text_hash", "documents", type_="check")
    op.drop_index(op.f("ix_documents_content_hash"), table_name="documents")
    op.drop_index(op.f("ix_documents_clean_text_hash"), table_name="documents")
    op.drop_column("documents", "clean_text_hash")
