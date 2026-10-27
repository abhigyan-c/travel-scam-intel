from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class DuplicateGroup(Base):
    __tablename__ = "duplicate_groups"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_duplicate_group_fingerprint"),
        CheckConstraint(
            "fingerprint IS NULL OR fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_duplicate_group_fingerprint",
        ),
        CheckConstraint(
            "jsonb_typeof(detection_metadata) = 'object'", name="ck_duplicate_group_metadata"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    description: Mapped[str | None] = mapped_column(Text)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    detection_metadata: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
