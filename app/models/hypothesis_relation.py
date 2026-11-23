from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.hypothesis import Hypothesis


class HypothesisRelationType(StrEnum):
    SAME_AS = "SAME_AS"
    RELATED_TO = "RELATED_TO"
    SUBTYPE_OF = "SUBTYPE_OF"
    CONTRADICTS = "CONTRADICTS"


class HypothesisRelation(Base):
    __tablename__ = "hypothesis_relations"
    __table_args__ = (
        CheckConstraint(
            "source_hypothesis_id <> target_hypothesis_id", name="ck_hypothesis_relation_not_self"
        ),
        UniqueConstraint(
            "source_hypothesis_id",
            "target_hypothesis_id",
            "relation_type",
            name="uq_hypothesis_relation",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_hypothesis_id: Mapped[UUID] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="RESTRICT")
    )
    target_hypothesis_id: Mapped[UUID] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="RESTRICT"), index=True
    )
    relation_type: Mapped[HypothesisRelationType] = mapped_column(
        Enum(
            HypothesisRelationType,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            name="hypothesis_relation_type",
        )
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source_hypothesis: Mapped[Hypothesis] = relationship(
        foreign_keys=[source_hypothesis_id], lazy="raise"
    )
    target_hypothesis: Mapped[Hypothesis] = relationship(
        foreign_keys=[target_hypothesis_id], lazy="raise"
    )


Index(
    "uq_hypothesis_relation_symmetric",
    func.least(HypothesisRelation.source_hypothesis_id, HypothesisRelation.target_hypothesis_id),
    func.greatest(HypothesisRelation.source_hypothesis_id, HypothesisRelation.target_hypothesis_id),
    HypothesisRelation.relation_type,
    unique=True,
    postgresql_where=HypothesisRelation.relation_type != HypothesisRelationType.SUBTYPE_OF,
)
