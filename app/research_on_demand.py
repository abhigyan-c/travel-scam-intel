"""Operator entry point for the bounded location intelligence service."""

import argparse
import asyncio

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.resolution import resolve_location
from app.db.session import create_database_engine
from app.embeddings import Embedder
from app.integrations.fetch import Fetcher
from app.integrations.sarvam import Sarvam
from app.integrations.serper import Serper
from app.integrations.storage import create_storage_client
from app.on_demand import get_location_intelligence


async def main(args):
    settings = Settings()
    engine = create_database_engine(settings)
    storage = create_storage_client(settings)
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url.get_secret_value()))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            location = await resolve_location(session, args.location, create_provisional=False)
            if location.location_id is None:
                raise ValueError("Resolve the location with explicit geographic context first")
        result = await get_location_intelligence(
            {
                "sessions": sessions,
                "settings": settings,
                "storage": storage,
                "bucket": settings.s3_bucket,
                "redis": redis,
                "provider": Serper(settings.serper_api_key),
                "fetcher": Fetcher(),
                "llm": Sarvam(settings.sarvam_api_key),
                "embedder": Embedder(settings.embedding_cache_dir),
            },
            location.location_id,
            wait_for_initial=not args.no_wait,
        )
        print(result.model_dump_json(indent=2))
    finally:
        await redis.aclose()
        storage.close()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Read intelligence and research unseen/stale locations"
    )
    parser.add_argument("location")
    parser.add_argument("--no-wait", action="store_true")
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main(parser.parse_args()))
