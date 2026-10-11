import asyncio
from collections.abc import Awaitable

from botocore.client import BaseClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def check_database(engine: AsyncEngine) -> bool:
    async with engine.connect() as connection:
        return bool(
            await connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
            )
        )


async def check_redis(client: Redis) -> bool:
    return bool(await client.ping())


async def check_storage(client: BaseClient, bucket: str) -> bool:
    await asyncio.to_thread(client.head_bucket, Bucket=bucket)
    return True


async def run_check(check: Awaitable[bool], timeout: float) -> str:
    try:
        async with asyncio.timeout(timeout):
            return "ok" if await check else "unavailable"
    except Exception:
        # Never return connection errors that might contain credentials or infrastructure details.
        return "unavailable"


async def check_dependencies(
    engine: AsyncEngine, redis: Redis, storage: BaseClient, bucket: str, timeout: float
) -> dict[str, str]:
    checks = (check_database(engine), check_redis(redis), check_storage(storage, bucket))
    results = await asyncio.gather(*(run_check(check, timeout) for check in checks))
    return dict(zip(("postgres", "redis", "storage"), results, strict=True))
