import argparse
import asyncio
import json
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.independence import calculate_independence
from app.db.session import create_database_engine


async def main(args):
    engine = create_database_engine(Settings())
    try:
        async with async_sessionmaker(engine)() as session:
            result = await calculate_independence(session, args.hypothesis_id, limit=args.limit)
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
            print(json.dumps({"dry_run": args.dry_run, **result.model_dump(mode="json")}, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Recalculate evidence independence for one hypothesis"
    )
    parser.add_argument("hypothesis_id", type=UUID)
    parser.add_argument("--limit", type=int, choices=range(1, 201), default=100)
    parser.add_argument("--dry-run", action="store_true")
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main(parser.parse_args()))
