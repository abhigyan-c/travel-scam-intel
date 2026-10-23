import argparse
import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.ingestion import DiscoveryResult, ingest_url
from app.db.session import create_database_engine
from app.integrations.fetch import Fetcher
from app.integrations.storage import create_storage_client


async def ingest(url: str):
    result = DiscoveryResult(url=url)
    settings = Settings()
    engine = create_database_engine(settings)
    storage = create_storage_client(settings)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            async with session.begin():
                document = await ingest_url(
                    session, result, fetcher=Fetcher(), storage=storage, bucket=settings.s3_bucket
                )
            print(document.id)
    finally:
        storage.close()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest one public article URL")
    parser.add_argument("url")
    args = parser.parse_args()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(ingest(args.url))
