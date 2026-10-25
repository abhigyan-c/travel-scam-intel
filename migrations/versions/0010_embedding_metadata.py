"""Track vector model, artifact/recipe revision and exact input hash."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("claims", "hypotheses"):
        op.add_column(table, sa.Column("embedding_model", sa.String(100), nullable=True))
        op.add_column(table, sa.Column("embedding_revision", sa.String(200), nullable=True))
        op.add_column(table, sa.Column("embedding_input_hash", sa.String(64), nullable=True))


def downgrade():
    for table in ("hypotheses", "claims"):
        for name in ("embedding_input_hash", "embedding_revision", "embedding_model"):
            op.drop_column(table, name)
