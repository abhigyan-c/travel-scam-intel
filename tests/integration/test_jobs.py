import asyncio
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from arq import Retry, create_pool
from arq.connections import RedisSettings
from arq.worker import Worker
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.jobs import create_job, dispatch_pending
from app.db.session import create_database_engine
from app.discovery import SearchResponse, SearchUsage
from app.integrations.fetch import FetchedPage
from app.integrations.storage import create_storage_client, ensure_bucket
from app.models import Document, ResearchJob, Source
from app.models.research_job import JobKind, JobStatus
from app.worker import run_job

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]


@pytest.fixture
async def environment():
    settings = Settings()
    engine = create_database_engine(settings)
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url.get_secret_value()))
    storage = create_storage_client(settings)
    ensure_bucket(storage, settings.s3_bucket)
    ctx = {
        "sessions": async_sessionmaker(engine, expire_on_commit=False),
        "redis": redis,
        "storage": storage,
        "bucket": settings.s3_bucket,
        "fetcher": AsyncMock(),
        "provider": AsyncMock(),
        "llm": AsyncMock(),
    }
    ctx["provider"].search.return_value = SearchResponse(usage=SearchUsage())
    jobs = []
    queue = f"test-jobs:{uuid4()}"
    yield ctx, jobs, queue
    try:
        async with ctx["sessions"]() as session:
            rows = list(await session.scalars(select(ResearchJob).where(ResearchJob.id.in_(jobs))))
            for job in rows:
                if job.kind == JobKind.INGEST and job.result.get("document_id"):
                    from uuid import UUID

                    document = await session.get(Document, UUID(job.result["document_id"]))
                    if document:
                        storage.delete_object(
                            Bucket=settings.s3_bucket, Key=document.raw_object_path
                        )
                        await session.delete(document)
                        await session.flush()
                        await session.execute(delete(Source).where(Source.id == document.source_id))
            await session.execute(delete(ResearchJob).where(ResearchJob.id.in_(jobs)))
            await session.commit()
        for identity in jobs:
            key = f"research:{identity}"
            await redis.zrem(queue, key)
            await redis.delete(
                f"arq:job:{key}", f"arq:retry:{key}", f"arq:result:{key}", f"arq:in-progress:{key}"
            )
        await redis.delete(queue, queue + ":health-check")
    finally:
        storage.close()
        await redis.aclose()
        await engine.dispose()


async def submit(environment, kind=JobKind.RESEARCH, payload=None, key=None):
    ctx, jobs, _ = environment
    async with ctx["sessions"]() as session:
        job = await create_job(
            session,
            kind=kind,
            payload=payload or {"location": "Fixture city"},
            idempotency_key=key or str(uuid4()),
        )
        await session.commit()
        if job.id not in jobs:
            jobs.append(job.id)
        return job.id


async def read(environment, identity):
    async with environment[0]["sessions"]() as session:
        return await session.get(ResearchJob, identity)


async def test_durable_submission_deduplicates_and_rejects_payload_change(environment):
    key = str(uuid4())
    first = await submit(environment, key=key)
    assert await submit(environment, key=key) == first
    with pytest.raises(ValueError, match="different work"):
        await submit(environment, key=key, payload={"location": "Another city"})
    assert (await read(environment, first)).status == JobStatus.QUEUED


@pytest.mark.skipif(os.name == "nt", reason="arq worker shutdown uses Unix signals; run in Docker")
async def test_redis_delivery_and_database_success_survive_redelivery(environment):
    ctx, _, queue = environment
    identity = await submit(environment)
    async with ctx["sessions"]() as session:
        assert (
            await dispatch_pending(session, ctx["redis"], queue_name=queue, job_ids=[identity]) == 1
        )
        assert (
            await dispatch_pending(session, ctx["redis"], queue_name=queue, job_ids=[identity]) == 0
        )
    worker = Worker(
        [run_job],
        redis_pool=ctx["redis"],
        queue_name=queue,
        ctx=ctx,
        burst=True,
        handle_signals=False,
        keep_result=0,
    )
    try:
        await worker.async_run()
    finally:
        await worker.close()
    job = await read(environment, identity)
    assert job.status == JobStatus.SUCCEEDED and job.attempts == 1
    assert job.result["stage"] == "claims" and job.result["status"] == "empty"
    await run_job(ctx, str(identity))
    assert (await read(environment, identity)).attempts == 1


async def test_ingestion_and_processing_jobs_share_real_document_path(environment):
    ctx = environment[0]
    url = f"https://example.org/worker/{uuid4()}"
    text = (
        "The traveler reported an unexpected additional charge after agreeing to a fixed fare. " * 8
    ).encode()
    ctx["fetcher"].fetch.return_value = FetchedPage(url, text, "text/plain")
    ingest = await submit(environment, JobKind.INGEST, {"url": url})
    await run_job(ctx, str(ingest))
    job = await read(environment, ingest)
    assert job.status == JobStatus.SUCCEEDED
    processing = await submit(
        environment, JobKind.PROCESS, {"document_id": job.result["document_id"]}
    )
    await run_job(ctx, str(processing))
    processed = await read(environment, processing)
    assert processed.status == JobStatus.SUCCEEDED
    assert processed.result["processing"]["eligible_for_extraction"] is True


async def test_retry_attempts_are_bounded_and_errors_sanitized(environment):
    ctx = environment[0]
    ctx["provider"].search.side_effect = RuntimeError("private payload must not be persisted")
    identity = await submit(environment)
    for _ in range(2):
        with pytest.raises(Retry):
            await run_job(ctx, str(identity))
    await run_job(ctx, str(identity))
    job = await read(environment, identity)
    assert job.status == JobStatus.FAILED and job.attempts == 3
    assert job.error_code == "RuntimeError" and job.run_token is None
    await run_job(ctx, str(identity))
    assert (await read(environment, identity)).attempts == 3


async def test_expired_worker_lease_is_recovered(environment):
    identity = await submit(environment)
    async with environment[0]["sessions"]() as session:
        job = await session.get(ResearchJob, identity)
        job.status, job.attempts, job.run_token = JobStatus.RUNNING, 1, uuid4()
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    await run_job(environment[0], str(identity))
    job = await read(environment, identity)
    assert job.status == JobStatus.SUCCEEDED and job.attempts == 2


async def test_live_lease_prevents_duplicate_work(environment):
    ctx = environment[0]
    identity = await submit(environment)

    async def slow_search(query):
        await asyncio.sleep(0.05)
        return SearchResponse(usage=SearchUsage())

    ctx["provider"].search.side_effect = slow_search
    outcomes = await asyncio.gather(
        run_job(ctx, str(identity)), run_job(ctx, str(identity)), return_exceptions=True
    )
    assert sum(isinstance(value, Retry) for value in outcomes) == 1
    assert (await read(environment, identity)).attempts == 1


async def test_cancelled_worker_releases_lease_for_redelivery(environment):
    ctx = environment[0]
    identity = await submit(environment)
    ctx["provider"].search.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await run_job(ctx, str(identity))
    cancelled = await read(environment, identity)
    assert cancelled.status == JobStatus.RETRY and cancelled.run_token is None
    ctx["provider"].search.side_effect = None
    await run_job(ctx, str(identity))
    assert (await read(environment, identity)).status == JobStatus.SUCCEEDED


async def test_stale_worker_cannot_overwrite_new_owner(environment):
    ctx = environment[0]
    identity = await submit(environment)
    replacement_token = uuid4()

    async def replace_owner(query):
        async with ctx["sessions"]() as session:
            job = await session.get(ResearchJob, identity)
            job.run_token = replacement_token
            await session.commit()
        return SearchResponse(usage=SearchUsage())

    ctx["provider"].search.side_effect = replace_owner
    await run_job(ctx, str(identity))
    job = await read(environment, identity)
    assert job.status == JobStatus.RUNNING and job.run_token == replacement_token
    assert job.result == {}


async def test_submission_survives_redis_outage_and_status_needs_only_database(
    environment, monkeypatch, capsys
):
    from argparse import Namespace

    from app import jobs as cli

    key = str(uuid4())
    unavailable = AsyncMock(side_effect=OSError("redis unavailable"))
    monkeypatch.setattr(cli, "create_pool", unavailable)
    with pytest.raises(OSError):
        await cli.main(Namespace(operation="research", value="Fixture city", key=key))
    async with environment[0]["sessions"]() as session:
        job = await session.scalar(select(ResearchJob).where(ResearchJob.idempotency_key == key))
        assert job.status == JobStatus.QUEUED
        environment[1].append(job.id)
        identity = job.id
    unavailable.reset_mock()
    await cli.main(Namespace(operation="status", value=str(identity), key=None))
    unavailable.assert_not_awaited()
    assert "queued" in capsys.readouterr().out
