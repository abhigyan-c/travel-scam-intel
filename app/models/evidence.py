from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, Float, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.claim import Claim
from app.models.duplicate_group import DuplicateGroup
from app.models.hypothesis import Hypothesis


class EvidenceStance(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    RELATED = "related"
    UNCERTAIN = "uncertain"


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            "(archived_at IS NULL AND superseded_by_id IS NULL) OR "
            "(archived_at IS NOT NULL AND superseded_by_id IS NOT NULL AND superseded_by_id <> id)",
            name="ck_evidence_archive_state",
        ),
        UniqueConstraint("hypothesis_id", "claim_id", name="uq_evidence_attachment"),
        CheckConstraint("semantic_match_score BETWEEN 0 AND 1", name="ck_evidence_semantic_match"),
        CheckConstraint("source_quality_score BETWEEN 0 AND 1", name="ck_evidence_source_quality"),
        CheckConstraint("independence_score BETWEEN 0 AND 1", name="ck_evidence_independence"),
        CheckConstraint("evidence_weight BETWEEN 0 AND 1", name="ck_evidence_weight"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    hypothesis_id: Mapped[UUID] = mapped_column(ForeignKey("hypotheses.id", ondelete="RESTRICT"))
    claim_id: Mapped[UUID] = mapped_column(ForeignKey("claims.id", ondelete="RESTRICT"), index=True)
    stance: Mapped[EvidenceStance] = mapped_column(
        Enum(
            EvidenceStance,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            name="evidence_stance",
        )
    )
    semantic_match_score: Mapped[float | None] = mapped_column(Float)
    source_quality_score: Mapped[float | None] = mapped_column(Float)
    independence_score: Mapped[float | None] = mapped_column(Float)
    evidence_weight: Mapped[float | None] = mapped_column(Float)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evidence.id", ondelete="RESTRICT"),
        index=True,
    )
    duplicate_group_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("duplicate_groups.id", ondelete="RESTRICT", name="fk_evidence_duplicate_group"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    hypothesis: Mapped[Hypothesis] = relationship(lazy="raise")
    claim: Mapped[Claim] = relationship(lazy="raise")
    duplicate_group: Mapped[DuplicateGroup | None] = relationship(lazy="raise")
