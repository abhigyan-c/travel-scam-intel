import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from arq import Retry
from arq.connections import RedisSettings
from arq.cron import cron
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.ingestion import DiscoveryResult, ingest_url
from app.db.jobs import dispatch_pending
from app.db.processing import process_document
from app.db.reports import process_report
from app.db.session import create_database_engine
from app.embeddings import Embedder
from app.integrations.fetch import Fetcher
from app.integrations.sarvam import Sarvam
from app.integrations.serper import Serper
from app.integrations.storage import create_storage_client
from app.models.document import DocumentStatus
from app.models.research_job import JobKind, JobStatus, ResearchJob
from app.models.user_report import UserReport
from app.research import research_location

logger = logging.getLogger("app.worker")


async def startup(ctx):
    settings = Settings()
    ctx["settings"] = settings
    ctx["embedder"] = Embedder(settings.embedding_cache_dir)
    configure_logging(settings.log_level)
    ctx["engine"] = create_database_engine(settings)
    ctx["sessions"] = async_sessionmaker(ctx["engine"], expire_on_commit=False)
    ctx["storage"] = create_storage_client(settings)
    ctx["bucket"] = settings.s3_bucket
    ctx["fetcher"] = Fetcher()
    ctx["provider"] = Serper(settings.serper_api_key)
    ctx["llm"] = Sarvam(settings.sarvam_api_key)
    async with ctx["sessions"]() as session:
        await dispatch_pending(session, ctx["redis"])


async def shutdown(ctx):
    ctx["storage"].close()
    await ctx["engine"].dispose()


async def _perform(ctx, kind, payload, session, identity, token):
    if kind == JobKind.REPORT:
        return await process_report(
            session, UUID(payload["report_id"]), client=ctx["llm"], embedder=ctx["embedder"]
        )
    if kind == JobKind.INGEST:
        document = await ingest_url(
            session,
            DiscoveryResult.model_validate(payload),
            fetcher=ctx["fetcher"],
            storage=ctx["storage"],
            bucket=ctx["bucket"],
        )
        return {"document_id": str(document.id)}
    if kind == JobKind.PROCESS:
        document = await process_document(
            session, UUID(payload["document_id"]), storage=ctx["storage"], bucket=ctx["bucket"]
        )
        if document is None:
            raise ValueError("document_missing")
        if document.processing_status == DocumentStatus.FAILED:
            # Persist the processing outcome even if the job will retry.
            owned = await session.scalar(
                select(ResearchJob.id)
                .where(ResearchJob.id == identity, ResearchJob.run_token == token)
                .with_for_update()
            )
            if owned is not None:
                await session.commit()
            else:
                await session.rollback()
            raise ValueError("document_processing_failed")
        return {
            "document_id": str(document.id),
            "processing": document.document_metadata["processing"],
        }
    result = await research_location(
        session,
        payload["location"],
        provider=ctx["provider"],
        fetcher=ctx["fetcher"],
        llm=ctx["llm"],
        storage=ctx["storage"],
        bucket=ctx["bucket"],
    )
    return result.model_dump(mode="json")


async def run_job(ctx, job_id: str):
    identity, token = UUID(job_id), uuid4()
    async with ctx["sessions"]() as session:
        kind = await session.scalar(select(ResearchJob.kind).where(ResearchJob.id == identity))
    if kind == JobKind.LOCATION_RESEARCH:
        from app.on_demand import run_location_job

        await run_location_job(ctx, job_id)
        return
    async with ctx["sessions"]() as session:
        async with session.begin():
            job = await session.scalar(
                select(ResearchJob).where(ResearchJob.id == identity).with_for_update()
            )
            if job is None or job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED}:
                return
            if job.status == JobStatus.RUNNING and job.lease_expires_at > datetime.now(UTC):
                raise Retry(defer=95)
            if job.attempts >= 3:
                job.status, job.run_token, job.lease_expires_at = JobStatus.FAILED, None, None
                job.error_code = "attempts_exhausted"
                if job.kind == JobKind.REPORT:
                    report = await session.get(UserReport, UUID(job.payload["report_id"]))
                    if report is not None and report.processing_status not in {
                        "processed",
                        "needs_review",
                    }:
                        report.processing_status = "failed"
                        report.processing_details = {
                            **report.processing_details,
                            "error_code": "attempts_exhausted",
                        }
                return
            job.attempts += 1
            job.status, job.run_token = JobStatus.RUNNING, token
            job.lease_expires_at = datetime.now(UTC) + timedelta(seconds=90)
            kind, payload, attempt = job.kind, job.payload, job.attempts
    error = None
    cancelled = False
    try:
        async with asyncio.timeout(55):
            async with ctx["sessions"]() as session:
                result = await _perform(ctx, kind, payload, session, identity, token)
                # Fence stale workers before committing their domain writes.
                owned = await session.scalar(
                    select(ResearchJob.id)
                    .where(ResearchJob.id == identity, ResearchJob.run_token == token)
                    .with_for_update()
                )
                if owned is None:
                    await session.rollback()
                    return
                report_retry = kind == JobKind.REPORT and result.get("retry")
                await session.execute(
                    update(ResearchJob)
                    .where(ResearchJob.id == identity)
                    .values(
                        status=JobStatus.RETRY
                        if report_retry and attempt < 3
                        else JobStatus.FAILED
                        if report_retry
                        or (kind == JobKind.RESEARCH and result.get("status") == "failed")
                        else JobStatus.SUCCEEDED,
                        result=result,
                        run_token=None,
                        lease_expires_at=None,
                        error_code="report_incomplete"
                        if report_retry
                        else "research_failed"
                        if kind == JobKind.RESEARCH and result.get("status") == "failed"
                        else None,
                    )
                )
                await session.commit()
                if report_retry and attempt < 3:
                    raise Retry(defer=attempt * 2)
                return
    except Retry:
        raise
    except asyncio.CancelledError:
        error, cancelled = "cancelled", True
    except Exception as exc:
        error = type(exc).__name__
    async with ctx["sessions"]() as session:
        if kind == JobKind.REPORT and attempt >= 3:
            owned = await session.scalar(
                select(ResearchJob.id)
                .where(ResearchJob.id == identity, ResearchJob.run_token == token)
                .with_for_update()
            )
            if owned is not None:
                report = await session.get(UserReport, UUID(payload["report_id"]))
                if report is not None and report.processing_status not in {
                    "processed",
                    "needs_review",
                }:
                    report.processing_status = "failed"
                    report.processing_details = {**report.processing_details, "error_code": error}
        await session.execute(
            update(ResearchJob)
            .where(ResearchJob.id == identity, ResearchJob.run_token == token)
            .values(
                status=JobStatus.RETRY if attempt < 3 else JobStatus.FAILED,
                run_token=None,
                lease_expires_at=None,
                error_code=error,
            )
        )
        await session.commit()
    logger.warning("job attempt failed", extra={"job_id": job_id, "error_type": error})
    if cancelled:
        raise asyncio.CancelledError
    if attempt < 3:
        raise Retry(defer=attempt * 2)


async def redispatch_jobs(ctx):
    async with ctx["sessions"]() as session:
        await dispatch_pending(session, ctx["redis"], kind=JobKind.LOCATION_RESEARCH)
        await dispatch_pending(session, ctx["redis"], kind=JobKind.REPORT)


class WorkerSettings:
    functions = [run_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(Settings().redis_url.get_secret_value())
    max_jobs = 4
    job_timeout = 60
    max_tries = 10
    keep_result = 0
    health_check_interval = 10
    cron_jobs = [cron(redispatch_jobs, second={0, 15, 30, 45}, run_at_startup=True)]
