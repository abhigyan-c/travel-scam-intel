from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.ingestion import DiscoveryResult
from app.discovery import generate_queries
from app.models.research_job import JobKind, JobStatus, ResearchJob


def validate_payload(kind: JobKind, payload: dict) -> dict:
    if kind == JobKind.INGEST:
        return DiscoveryResult.model_validate(payload).model_dump(mode="json")
    if kind == JobKind.PROCESS:
        return {"document_id": str(UUID(payload["document_id"]))}
    location = payload["location"]
    generate_queries(location)
    return {"location": " ".join(location.split())}


async def create_job(
    session: AsyncSession, *, kind: JobKind, payload: dict, idempotency_key: str
) -> ResearchJob:
    kind = JobKind(kind)
    payload = validate_payload(kind, payload)
    if not idempotency_key.strip() or len(idempotency_key) > 200:
        raise ValueError("invalid idempotency key")
    identity = await session.scalar(
        insert(ResearchJob)
        .values(kind=kind, payload=payload, idempotency_key=idempotency_key)
        .on_conflict_do_nothing(index_elements=[ResearchJob.idempotency_key])
        .returning(ResearchJob.id)
    )
    job = (
        await session.get(ResearchJob, identity)
        if identity
        else await session.scalar(
            select(ResearchJob).where(ResearchJob.idempotency_key == idempotency_key)
        )
    )
    if job.kind != kind or job.payload != payload:
        raise ValueError("idempotency key already used for different work")
    return job


async def dispatch_pending(
    session: AsyncSession,
    redis,
    *,
    queue_name: str = "arq:queue",
    limit: int = 100,
    job_ids: list[UUID] | None = None,
) -> int:
    if not 1 <= limit <= 1000:
        raise ValueError("invalid dispatch limit")
    jobs = await session.scalars(
        select(ResearchJob.id)
        .where(ResearchJob.id.in_(job_ids) if job_ids is not None else True)
        .where(
            or_(
                ResearchJob.status.in_([JobStatus.QUEUED, JobStatus.RETRY]),
                (ResearchJob.status == JobStatus.RUNNING)
                & (ResearchJob.lease_expires_at <= func.now()),
            )
        )
        .order_by(ResearchJob.created_at, ResearchJob.id)
        .limit(limit)
    )
    count = 0
    for identity in jobs:
        queued = await redis.enqueue_job(
            "run_job", str(identity), _job_id=f"research:{identity}", _queue_name=queue_name
        )
        count += queued is not None
    return count
