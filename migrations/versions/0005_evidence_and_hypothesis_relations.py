"""evidence and hypothesis relations"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hypothesis_relations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_hypothesis_id", sa.Uuid(), nullable=False),
        sa.Column("target_hypothesis_id", sa.Uuid(), nullable=False),
        sa.Column(
            "relation_type",
            sa.Enum(
                "SAME_AS",
                "RELATED_TO",
                "SUBTYPE_OF",
                "CONTRADICTS",
                name="hypothesis_relation_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_hypothesis_id <> target_hypothesis_id", name="ck_hypothesis_relation_not_self"
        ),
        sa.ForeignKeyConstraint(["source_hypothesis_id"], ["hypotheses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_hypothesis_id"], ["hypotheses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_hypothesis_id",
            "target_hypothesis_id",
            "relation_type",
            name="uq_hypothesis_relation",
        ),
    )
    op.create_index(
        op.f("ix_hypothesis_relations_target_hypothesis_id"),
        "hypothesis_relations",
        ["target_hypothesis_id"],
        unique=False,
    )
    op.create_index(
        "uq_hypothesis_relation_symmetric",
        "hypothesis_relations",
        [
            sa.literal_column("least(source_hypothesis_id, target_hypothesis_id)"),
            sa.literal_column("greatest(source_hypothesis_id, target_hypothesis_id)"),
            "relation_type",
        ],
        unique=True,
        postgresql_where=sa.text("relation_type != 'SUBTYPE_OF'"),
    )
    op.create_table(
        "evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("hypothesis_id", sa.Uuid(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column(
            "stance",
            sa.Enum(
                "supports",
                "contradicts",
                "related",
                "uncertain",
                name="evidence_stance",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("semantic_match_score", sa.Float(), nullable=True),
        sa.Column("source_quality_score", sa.Float(), nullable=True),
        sa.Column("independence_score", sa.Float(), nullable=True),
        sa.Column("evidence_weight", sa.Float(), nullable=True),
        sa.Column("duplicate_group_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("evidence_weight BETWEEN 0 AND 1", name="ck_evidence_weight"),
        sa.CheckConstraint("independence_score BETWEEN 0 AND 1", name="ck_evidence_independence"),
        sa.CheckConstraint(
            "semantic_match_score BETWEEN 0 AND 1", name="ck_evidence_semantic_match"
        ),
        sa.CheckConstraint(
            "source_quality_score BETWEEN 0 AND 1", name="ck_evidence_source_quality"
        ),
        sa.ForeignKeyConstraint(["claim_id"], ["claims.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["hypothesis_id"], ["hypotheses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hypothesis_id", "claim_id", name="uq_evidence_attachment"),
    )
    op.create_index(op.f("ix_evidence_claim_id"), "evidence", ["claim_id"], unique=False)
    op.create_index(
        op.f("ix_evidence_duplicate_group_id"), "evidence", ["duplicate_group_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_evidence_duplicate_group_id"), table_name="evidence")
    op.drop_index(op.f("ix_evidence_claim_id"), table_name="evidence")
    op.drop_table("evidence")
    op.drop_index(
        "uq_hypothesis_relation_symmetric",
        table_name="hypothesis_relations",
        postgresql_where=sa.text("relation_type != 'SUBTYPE_OF'"),
    )
    op.drop_index(
        op.f("ix_hypothesis_relations_target_hypothesis_id"), table_name="hypothesis_relations"
    )
    op.drop_table("hypothesis_relations")
