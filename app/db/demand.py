from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert

from app.frequency import utc
from app.models import LocationDemandHour


async def record_demand(session, location_id, *, as_of=None):
    hour = utc(as_of or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)
    statement = insert(LocationDemandHour).values(location_id=location_id, hour=hour, requests=1)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[LocationDemandHour.location_id, LocationDemandHour.hour],
            set_={"requests": LocationDemandHour.requests + 1},
        )
    )
