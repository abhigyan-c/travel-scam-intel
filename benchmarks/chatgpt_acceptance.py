"""Temporary ChatGPT host acceptance: real UI/intake, synthetic delayed report.

Loopback only. Local disposable PostgreSQL schema; private in-memory raw objects;
no real Redis/worker, production data, R2 or provider credentials. Never production.
"""

import asyncio
import hashlib
import sys
import time
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.routing import Route

from app.core.config import Settings
from app.db.locations import normalize_name
from app.db.session import Base, create_database_engine
from app.mcp_server import build_mcp, failure
from app.models import Document, Location, ResearchJob, UserReport
from app.schemas.research_report import ResearchReport

PUBLIC_URL = "https://travel-scam-prod.tailbbf557.ts.net/mcp/acceptance"
LOCAL_DB = "postgresql+psycopg://travel:travel-local@127.0.0.1:5432/travel_scam_intel"
LOCATION = UUID("9e5c94b0-efac-4fe3-ab71-314d7c483400")
NAME = "Acceptance fixture (synthetic; not a real destination)"


class MemoryStorage:
    def __init__(self):
        self.objects = {}

    def put_object(self, **kwargs):
        self.objects.setdefault(kwargs["Key"], bytes(kwargs["Body"]))


class NoWorker:
    def __init__(self):
        self.dispatches = []

    async def enqueue_job(self, *args, **kwargs):
        self.dispatches.append(kwargs.get("_job_id"))
        return object()


class DelayedReport:
    """Same request/get/wait adapter interface; controlled five-minute empty result."""

    def __init__(self, sessions, *, clock=time.monotonic):
        self.sessions = sessions
        self.clock = clock
        self.run = uuid4()
        self.start = None
        self.started_at = None
        self.watches = {}
        self.requests = 0

    async def get(self, run_id, *, limit=5, offset=0, watch_id=None):
        if run_id != self.run:
            from app.services.intelligence import IntelligenceNotFound

            raise IntelligenceNotFound("fixture_run_not_found")
        elapsed = self.clock() - self.start if self.start is not None else 0
        terminal = elapsed >= 300
        state = "completed" if terminal else "researching" if elapsed >= 10 else "queued"
        finished = self.started_at + timedelta(seconds=300) if terminal else None
        watch = self.watches.get(watch_id)
        return ResearchReport.model_validate(
            dict(
                contract_version="research-report-v1",
                run_id=self.run,
                location=dict(
                    id=LOCATION, name=NAME, type="country", country_code="ZZ", provisional=False
                ),
                revision=hashlib.sha256(f"{state}:{offset}:{limit}".encode()).hexdigest(),
                latest_run=dict(
                    status=state,
                    terminal=terminal,
                    created_at=self.started_at,
                    started_at=self.started_at,
                    last_checked_at=finished,
                    stop_reasons=[],
                ),
                retained_report=dict(
                    coverage="complete" if terminal else "unknown",
                    freshness="fresh" if terminal else "unknown",
                    fresh_until=finished + timedelta(days=1) if finished else None,
                    last_result_update_at=None,
                    last_completed_at=finished,
                ),
                superseded=False,
                items=[],
                total=0,
                limit=limit,
                offset=offset,
                has_more=False,
                membership_limit=1000,
                membership_may_be_truncated=False,
                outcome="nothing_found" if terminal else "research_incomplete",
                next_check_after_seconds=1,
                watch=dict(
                    id=watch_id, expires_at=watch["expires"], waits_remaining=60 - watch["waits"]
                )
                if watch
                else None,
            )
        ).model_dump(mode="json")

    async def request(self, ctx, location_id, *, follow_run_id=None, watch_seconds=300):
        if location_id != LOCATION or (follow_run_id is not None and follow_run_id != self.run):
            from app.services.intelligence import IntelligenceNotFound

            raise IntelligenceNotFound("fixture_location_not_found")
        if self.start is None:
            self.start, self.started_at = self.clock(), datetime.now(UTC)
        self.requests += 1
        watch = uuid4()
        self.watches[watch] = dict(
            expires=datetime.now(UTC) + timedelta(seconds=watch_seconds), waits=0
        )
        return await self.get(self.run, watch_id=watch)

    async def wait(self, run_id, watch_id, after_revision, *, wait_seconds=3, limit=5, offset=0):
        watch = self.watches[watch_id]
        status = None
        if watch["expires"] <= datetime.now(UTC):
            status = "expired"
        elif watch["waits"] >= 60:
            status = "limit_reached"
        else:
            watch["waits"] += 1
        until = time.monotonic() + min(wait_seconds, 5)
        while status is None:
            result = await self.get(run_id, limit=limit, offset=offset, watch_id=watch_id)
            if result["latest_run"]["terminal"]:
                status = "terminal"
            elif result["revision"] != after_revision:
                status = "changed"
            elif time.monotonic() >= until:
                status = "unchanged"
            else:
                await asyncio.sleep(0.25)
        return {
            **await self.get(run_id, limit=limit, offset=offset, watch_id=watch_id),
            "wait_status": status,
        }


settings = Settings(
    _env_file=None,
    environment="test",
    database_url=LOCAL_DB,
    serper_api_key="",
    sarvam_api_key="",
    admin_api_token="",
    s3_access_key="synthetic-only",
    s3_secret_key="synthetic-only",
    s3_bucket="private-memory-only",
    mcp_public_url=PUBLIC_URL,
    mcp_public_aliases=[],
)
metrics = []
simulate_receipt_loss = "--receipt-loss" in sys.argv
lost_receipt = False


@asynccontextmanager
async def lifespan(app):
    schema = "chatgpt_acceptance_" + uuid4().hex
    admin = create_database_engine(settings)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        LOCAL_DB, connect_args={"options": f"-csearch_path={schema},public"}, hide_parameters=True
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, checkfirst=False))
        app.state.db_sessions = async_sessionmaker(engine, expire_on_commit=False)
        app.state.research_reports = DelayedReport(app.state.db_sessions)
        app.state.storage = MemoryStorage()
        app.state.report_queue = NoWorker()
        app.state.embedder = None
        app.state.schema = schema
        async with app.state.db_sessions() as session, session.begin():
            session.add(
                Location(
                    id=LOCATION,
                    name=NAME,
                    canonical_name=normalize_name(NAME),
                    type="country",
                    country_code="ZZ",
                )
            )
        async with server.session_manager.run():
            yield
    finally:
        await engine.dispose()
        # Exactly the disposable local schema this process created, never public/production.
        assert schema.startswith("chatgpt_acceptance_") and schema[19:].isalnum()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


application = FastAPI(lifespan=lifespan)
application.state.settings = settings
server, transport = build_mcp(application)
original_call = server.call_tool


async def measured_call(name, arguments, context=None):
    global lost_receipt
    start = time.monotonic()
    result = await original_call(name, arguments, context)
    if (
        simulate_receipt_loss
        and not lost_receipt
        and name == "submit_scam_report"
        and arguments.get("confirmed_by_user") is True
        and not getattr(result, "is_error", False)
    ):
        lost_receipt = True
        result = failure("unavailable", "Synthetic receipt loss after isolated test commit.")
    metrics.append(
        dict(
            tool=name,
            elapsed_ms=round((time.monotonic() - start) * 1000, 2),
            error=bool(getattr(result, "is_error", False)),
        )
    )
    return result


server.call_tool = measured_call
application.router.routes.append(
    Route("/mcp", endpoint=transport, methods=["GET", "POST", "DELETE"])
)


@application.get("/stats")
async def stats():
    async with application.state.db_sessions() as session:
        counts = {
            model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in [UserReport, Document, ResearchJob]
        }
    delayed = application.state.research_reports
    return dict(
        synthetic=True,
        production_writes=0,
        provider_calls=0,
        database="disposable localhost schema",
        raw_storage="private process memory",
        counts=counts,
        stored_objects=len(application.state.storage.objects),
        synthetic_receipt_losses=int(lost_receipt),
        fixture_roots=1 if delayed.start is not None else 0,
        fixture_status=(await delayed.get(delayed.run))["latest_run"],
        watches=[dict(waits=w["waits"], expires_at=w["expires"]) for w in delayed.watches.values()],
        tool_counts=dict(Counter(m["tool"] for m in metrics)),
        calls=metrics[-200:],
    )


if __name__ == "__main__":
    import uvicorn

    config = uvicorn.Config(
        application, host="127.0.0.1", port=8034, access_log=False, log_level="warning"
    )
    asyncio.run(uvicorn.Server(config).serve(), loop_factory=asyncio.SelectorEventLoop)
