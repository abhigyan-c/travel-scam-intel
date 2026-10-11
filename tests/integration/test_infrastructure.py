import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.integrations.storage import ensure_bucket
from app.main import create_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]


@pytest.fixture
async def application():
    application = create_app(Settings())
    async with application.router.lifespan_context(application):
        yield application


async def test_database_sessions_and_pgvector(application):
    async with application.state.db_sessions() as session:
        assert await session.scalar(text("SELECT 1")) == 1
        assert await session.scalar(text("SELECT '[1,0,0]'::vector <=> '[1,0,0]'::vector")) == 0
        assert await session.scalar(text("SELECT version_num FROM alembic_version")) == "0001"


async def test_redis_round_trip(application):
    key = f"integration:{uuid4()}"
    redis = application.state.redis
    try:
        await redis.set(key, "test", ex=60)
        assert await redis.get(key) == b"test"
    finally:
        await redis.delete(key)


async def test_storage_round_trip_and_repeat_bootstrap(application):
    storage = application.state.storage
    bucket = application.state.settings.s3_bucket
    key = f"integration/{uuid4()}"
    await asyncio.to_thread(ensure_bucket, storage, bucket)
    await asyncio.to_thread(ensure_bucket, storage, bucket)
    try:
        await asyncio.to_thread(storage.put_object, Bucket=bucket, Key=key, Body=b"source fixture")
        result = await asyncio.to_thread(storage.get_object, Bucket=bucket, Key=key)
        try:
            assert await asyncio.to_thread(result["Body"].read) == b"source fixture"
        finally:
            result["Body"].close()
    finally:
        await asyncio.to_thread(storage.delete_object, Bucket=bucket, Key=key)


async def test_readiness_with_real_dependencies(application):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {"postgres": "ok", "redis": "ok", "storage": "ok"}
