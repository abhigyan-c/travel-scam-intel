import argparse
import asyncio
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.frequency import calculate_recency_and_frequency
from app.db.session import create_database_engine
from app.frequency import utc


def reference_date(value):
    try:
        return utc(datetime.fromisoformat(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--as-of requires an ISO timestamp with timezone") from exc


async def main(args):
    engine = create_database_engine(Settings())
    try:
        async with async_sessionmaker(engine)() as session:
            result = await calculate_recency_and_frequency(
                session, args.hypothesis_id, as_of=args.as_of, limit=args.limit
            )
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
            print(json.dumps({"dry_run": args.dry_run, **result.model_dump(mode="json")}, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Measure occurrence recency and observed reporting frequency"
    )
    parser.add_argument("hypothesis_id", type=UUID)
    parser.add_argument("--as-of", type=reference_date)
    parser.add_argument("--limit", type=int, choices=range(1, 201), default=100)
    parser.add_argument("--dry-run", action="store_true")
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main(parser.parse_args()))
