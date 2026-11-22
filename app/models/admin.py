from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class AdminAction(Base):
    __tablename__ = "admin_actions"
    __table_args__ = (
        CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="ck_admin_key_hash"),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_admin_input_hash"),
        CheckConstraint("length(trim(reason)) BETWEEN 1 AND 2000", name="ck_admin_reason"),
        CheckConstraint("jsonb_typeof(result)='object'", name="ck_admin_result"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(40))
    subject_id: Mapped[UUID] = mapped_column(index=True)
    actor: Mapped[str] = mapped_column(String(100), default="admin-token")
    reason: Mapped[str] = mapped_column(Text)
    result: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
