from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.document import Document


class ReportProvenance(StrEnum):
    FIRST_HAND = "FIRST_HAND"
    WITNESSED = "WITNESSED"
    SECOND_HAND = "SECOND_HAND"


class UserReport(Base):
    __tablename__ = "user_reports"
    __table_args__ = (CheckConstraint("length(trim(report_text)) > 0", name="ck_user_report_text"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"), unique=True
    )
    location_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT"), index=True
    )
    report_text: Mapped[str] = mapped_column(Text)
    provenance_class: Mapped[ReportProvenance] = mapped_column(
        Enum(
            ReportProvenance,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            name="report_provenance",
        )
    )
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    document: Mapped[Document] = relationship(lazy="raise")
