"""Trusted local operator entry point; public/admin adapters will enforce authentication later."""

import argparse
import asyncio
import json
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.hypothesis_lifecycle import Review, apply_resolution
from app.db.session import create_database_engine
from app.embeddings import Embedder


async def main(args):
    review = Review(actor=args.reviewer, reason=args.reason) if args.reviewer else None
    engine = create_database_engine(Settings())
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            event = await apply_resolution(
                session, args.resolution_id, embedder=Embedder(), review=review
            )
            output = {
                "event_id": str(event.id),
                "hypothesis_id": str(event.target_id),
                "dry_run": args.dry_run,
                "details": event.details,
            }
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
            print(json.dumps(output, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Apply a recorded hypothesis decision")
    parser.add_argument("resolution_id", type=UUID)
    parser.add_argument("--reviewer")
    parser.add_argument("--reason")
    parser.add_argument("--dry-run", action="store_true", help="Preview then roll back all writes")
    args = parser.parse_args()
    if bool(args.reviewer) != bool(args.reason):
        parser.error("--reviewer and --reason must be supplied together")
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main(args))
