from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class LocationType(StrEnum):
    COUNTRY = "country"
    CITY = "city"
    NEIGHBORHOOD = "neighborhood"
    AREA = "area"
    POINT_OF_INTEREST = "point_of_interest"


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint(
            "parent_id",
            "canonical_name",
            "type",
            name="uq_location_identity",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_location_not_self_parent"),
        CheckConstraint("type <> 'country' OR parent_id IS NULL", name="ck_country_is_root"),
        CheckConstraint("length(trim(name)) > 0", name="ck_location_name"),
        CheckConstraint("length(trim(canonical_name)) > 0", name="ck_location_canonical_name"),
        CheckConstraint(
            "country_code IS NULL OR country_code ~ '^[A-Z]{2}$'", name="ck_country_code"
        ),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_latitude"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_longitude"),
        CheckConstraint("(latitude IS NULL) = (longitude IS NULL)", name="ck_coordinate_pair"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))
    canonical_name: Mapped[str] = mapped_column(String(200), index=True)
    type: Mapped[LocationType] = mapped_column(
        Enum(
            LocationType,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            name="location_type",
        )
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT"), index=True
    )
    country_code: Mapped[str | None] = mapped_column(String(2), index=True)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    is_provisional: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    aliases: Mapped[list["LocationAlias"]] = relationship(lazy="raise", passive_deletes=True)


class LocationAlias(Base):
    __tablename__ = "location_aliases"
    __table_args__ = (
        UniqueConstraint("location_id", "normalized_alias", name="uq_location_alias"),
        CheckConstraint("length(trim(alias)) > 0", name="ck_alias_name"),
        CheckConstraint("length(trim(normalized_alias)) > 0", name="ck_normalized_alias"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    location_id: Mapped[UUID] = mapped_column(
        ForeignKey("locations.id", ondelete="CASCADE"), index=True
    )
    alias: Mapped[str] = mapped_column(String(200))
    normalized_alias: Mapped[str] = mapped_column(String(200), index=True)
