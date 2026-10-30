"""private report media and moderation audit"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("upload_key_hash", sa.String(length=64), nullable=False),
        sa.Column("original_hash", sa.String(length=64), nullable=False),
        sa.Column("stored_hash", sa.String(length=64), nullable=False),
        sa.Column("original_size", sa.Integer(), nullable=False),
        sa.Column("stored_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=30), nullable=False),
        sa.Column("raw_object_path", sa.Text(), nullable=False),
        sa.Column("private_object_path", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="needs_review", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("media_type IN ('image/jpeg','image/png')", name="ck_attachment_type"),
        sa.CheckConstraint(
            "status IN ('needs_review','accepted','rejected')", name="ck_attachment_status"
        ),
        sa.CheckConstraint(
            "upload_key_hash ~ '^[0-9a-f]{64}$' AND original_hash ~ '^[0-9a-f]{64}$' "
            "AND stored_hash ~ '^[0-9a-f]{64}$'",
            name="ck_attachment_hashes",
        ),
        sa.CheckConstraint(
            "original_size BETWEEN 1 AND 5242880 AND stored_size BETWEEN 1 AND 5242880",
            name="ck_attachment_sizes",
        ),
        sa.CheckConstraint(
            "width > 0 AND height > 0 AND width::bigint * height <= 10000000",
            name="ck_attachment_dimensions",
        ),
        sa.ForeignKeyConstraint(["report_id"], ["user_reports.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id", "upload_key_hash", name="uq_report_attachment_upload"),
    )
    op.create_index(
        op.f("ix_report_attachments_report_id"), "report_attachments", ["report_id"], unique=False
    )
    op.create_table(
        "report_moderation_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("previous_status", sa.String(length=20), nullable=False),
        sa.Column("next_status", sa.String(length=20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('attachment_added','processing_completed','accepted',"
            "'rejected','needs_review')",
            name="ck_moderation_event_action",
        ),
        sa.CheckConstraint(
            "key_hash ~ '^[0-9a-f]{64}$' AND request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_moderation_event_hashes",
        ),
        sa.CheckConstraint("revision > 0", name="ck_moderation_event_revision"),
        sa.ForeignKeyConstraint(["report_id"], ["user_reports.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id", "key_hash", name="uq_report_moderation_key"),
    )
    op.create_index(
        op.f("ix_report_moderation_events_report_id"),
        "report_moderation_events",
        ["report_id"],
        unique=False,
    )
    op.add_column(
        "user_reports",
        sa.Column("moderation_revision", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_report_moderation_revision", "user_reports", "moderation_revision >= 0"
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM report_attachments) "
            "OR EXISTS (SELECT 1 FROM report_moderation_events) "
            "OR EXISTS (SELECT 1 FROM user_reports WHERE moderation_revision <> 0)"
        )
    ):
        raise RuntimeError("Preserve attachment records and moderation history before downgrading")
    op.drop_constraint("ck_report_moderation_revision", "user_reports", type_="check")
    op.drop_column("user_reports", "moderation_revision")
    op.drop_index(
        op.f("ix_report_moderation_events_report_id"), table_name="report_moderation_events"
    )
    op.drop_table("report_moderation_events")
    op.drop_index(op.f("ix_report_attachments_report_id"), table_name="report_attachments")
    op.drop_table("report_attachments")
