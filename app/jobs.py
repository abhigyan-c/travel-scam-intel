import argparse
import asyncio
from uuid import UUID

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.jobs import create_job, dispatch_pending
from app.db.session import create_database_engine
from app.models.research_job import JobKind, ResearchJob


async def main(args):
    settings = Settings()
    engine = create_database_engine(settings)
    redis = None
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            if args.operation == "status":
                job = await session.get(ResearchJob, UUID(args.value))
                if job is None:
                    raise ValueError("job not found")
                print(
                    {
                        "id": str(job.id),
                        "status": job.status.value,
                        "attempts": job.attempts,
                        "error_code": job.error_code,
                        "result": job.result,
                    }
                )
                return
            if args.operation == "dispatch":
                redis = await create_pool(
                    RedisSettings.from_dsn(settings.redis_url.get_secret_value())
                )
                print(await dispatch_pending(session, redis))
                return
            if not args.key or not args.value:
                raise ValueError("submission requires a value and --key")
            kind = JobKind(args.operation)
            field = {
                JobKind.INGEST: "url",
                JobKind.PROCESS: "document_id",
                JobKind.RESEARCH: "location",
            }[kind]
            async with session.begin():
                job = await create_job(
                    session, kind=kind, payload={field: args.value}, idempotency_key=args.key
                )
            print(job.id)
            # DB commit precedes Redis publication; dispatch recovers a failed publication.
            redis = await create_pool(RedisSettings.from_dsn(settings.redis_url.get_secret_value()))
            await redis.enqueue_job("run_job", str(job.id), _job_id=f"research:{job.id}")
    finally:
        if redis is not None:
            await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Submit, inspect or dispatch durable background jobs"
    )
    parser.add_argument(
        "operation", choices=["ingest", "process", "research", "status", "dispatch"]
    )
    parser.add_argument("value", nargs="?")
    parser.add_argument("--key")
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main(parser.parse_args()))
