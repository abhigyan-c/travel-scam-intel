from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Connection, create_engine, select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import Settings
from app.db.locations import normalize_name
from app.models.location import Location, LocationAlias, LocationType

SEED_LOCATIONS = (
    ("Thailand", None, "TH", LocationType.COUNTRY),
    ("Bangkok", "Thailand", "TH", LocationType.CITY),
    ("Grand Palace", "Bangkok", "TH", LocationType.POINT_OF_INTEREST),
    ("Wat Pho", "Bangkok", "TH", LocationType.POINT_OF_INTEREST),
    ("Fiji", None, "FJ", LocationType.COUNTRY),
    ("Nadi", "Fiji", "FJ", LocationType.CITY),
    ("Suva", "Fiji", "FJ", LocationType.CITY),
)


def seed_locations(connection: Connection) -> None:
    ids = {}
    for name, parent, country_code, location_type in SEED_LOCATIONS:
        parent_id = ids[parent] if parent else None
        canonical = normalize_name(name)
        connection.execute(
            insert(Location)
            .values(
                id=uuid5(NAMESPACE_URL, f"travel-scam-intel/location/{name}"),
                name=name,
                canonical_name=canonical,
                parent_id=parent_id,
                type=location_type,
                country_code=country_code,
                is_provisional=False,
            )
            .on_conflict_do_nothing()
        )
        ids[name] = connection.execute(
            select(Location.id).where(
                Location.canonical_name == canonical,
                Location.parent_id == parent_id,
                Location.type == location_type,
            )
        ).scalar_one()
    connection.execute(
        insert(LocationAlias)
        .values(
            id=uuid5(NAMESPACE_URL, "travel-scam-intel/alias/BKK"),
            location_id=ids["Bangkok"],
            alias="BKK",
            normalized_alias=normalize_name("BKK"),
        )
        .on_conflict_do_nothing()
    )


if __name__ == "__main__":
    engine = create_engine(
        Settings().database_url.get_secret_value(), connect_args={"connect_timeout": 5}
    )
    try:
        with engine.begin() as connection:
            seed_locations(connection)
    finally:
        engine.dispose()
