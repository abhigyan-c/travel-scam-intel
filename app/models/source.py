from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SourceType(StrEnum):
    OFFICIAL_ADVISORY = "official_advisory"
    NEWS = "news"
    BLOG = "blog"
    FORUM = "forum"
    USER_REPORT = "user_report"
    OTHER = "other"


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint("length(trim(platform)) > 0", name="ck_source_platform"),
        CheckConstraint(
            "canonical_url IS NULL OR length(trim(canonical_url)) > 0", name="ck_source_url"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    platform: Mapped[str] = mapped_column(String(200))
    canonical_url: Mapped[str | None] = mapped_column(Text, unique=True)
    author_identifier: Mapped[str | None] = mapped_column(String(500))
    source_type: Mapped[SourceType] = mapped_column(
        Enum(
            SourceType,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            name="source_type",
        )
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # SQLAlchemy reserves `metadata`; retain the requested database column name.
    source_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
