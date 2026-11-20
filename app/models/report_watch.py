from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ReportWatch(Base):
    __tablename__ = "report_watches"
    __table_args__ = (CheckConstraint("waits BETWEEN 0 AND 60", name="ck_watch_waits"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="RESTRICT"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    waits: Mapped[int] = mapped_column(default=0)
