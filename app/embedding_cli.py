import argparse
import asyncio
import json
import time

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.embeddings import backfill_embeddings
from app.db.session import create_database_engine
from app.embeddings import Embedder
from app.models import Claim, Hypothesis


async def main(args):
    embedder = Embedder(args.cache_dir)
    started = time.perf_counter()
    await embedder.preload(download=args.download)
    output = {
        "model": embedder.model,
        "revision": embedder.revision,
        "preload_seconds": round(time.perf_counter() - started, 3),
    }
    if args.command == "backfill":
        engine = create_database_engine(Settings())
        try:
            async with async_sessionmaker(engine)() as session:
                async with session.begin():
                    for name, model in (("claims", Claim), ("hypotheses", Hypothesis)):
                        output[name] = await backfill_embeddings(
                            session,
                            model,
                            embedder,
                            limit=args.limit,
                        )
        finally:
            await engine.dispose()
    output["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Preload local embeddings or backfill stored rows")
    parser.add_argument("command", choices=["preload", "backfill"])
    parser.add_argument("--download", action="store_true", help="Allow pinned model download")
    parser.add_argument("--cache-dir", default=".tools/embedding-cache")
    parser.add_argument("--limit", type=int, default=100, help="Maximum rows per entity type")
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main(args))
