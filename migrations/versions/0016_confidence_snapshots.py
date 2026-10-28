"""Versioned confidence inputs without fabricated prevalence or recency."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("hypothesis_score_snapshots", "prevalence_score", nullable=True)
    op.alter_column("hypothesis_score_snapshots", "recency_score", nullable=True)
    op.add_column(
        "hypothesis_score_snapshots", sa.Column("input_hash", sa.String(64), nullable=True)
    )
    op.add_column(
        "hypothesis_score_snapshots", sa.Column("weight_run_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_score_snapshot_weight_run",
        "hypothesis_score_snapshots",
        "evidence_weight_runs",
        ["weight_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_hypothesis_score_snapshots_weight_run_id",
        "hypothesis_score_snapshots",
        ["weight_run_id"],
    )
    op.create_unique_constraint(
        "uq_score_snapshot_input",
        "hypothesis_score_snapshots",
        ["hypothesis_id", "scoring_version", "input_hash"],
    )
    op.create_check_constraint(
        "ck_score_snapshot_hash", "hypothesis_score_snapshots", "input_hash ~ '^[0-9a-f]{64}$'"
    )


def downgrade() -> None:
    # Unknown historical values must not be rewritten as measured zeroes.
    connection = op.get_bind()
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM hypothesis_score_snapshots "
            "WHERE prevalence_score IS NULL OR recency_score IS NULL)"
        )
    ):
        raise RuntimeError(
            "Cannot downgrade while confidence-only snapshots have unknown prevalence/recency"
        )
    op.drop_constraint("ck_score_snapshot_hash", "hypothesis_score_snapshots", type_="check")
    op.drop_constraint("uq_score_snapshot_input", "hypothesis_score_snapshots", type_="unique")
    op.drop_index(
        "ix_hypothesis_score_snapshots_weight_run_id", table_name="hypothesis_score_snapshots"
    )
    op.drop_constraint(
        "fk_score_snapshot_weight_run", "hypothesis_score_snapshots", type_="foreignkey"
    )
    op.drop_column("hypothesis_score_snapshots", "weight_run_id")
    op.drop_column("hypothesis_score_snapshots", "input_hash")
    op.alter_column("hypothesis_score_snapshots", "recency_score", nullable=False)
    op.alter_column("hypothesis_score_snapshots", "prevalence_score", nullable=False)
