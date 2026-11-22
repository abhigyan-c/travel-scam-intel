import asyncio
from contextlib import AsyncExitStack, asynccontextmanager

from arq.connections import ArqRedis
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.exceptions import HTTPException
from starlette.routing import Route

from app.api.admin import page_router as admin_page_router
from app.api.admin import router as admin_router
from app.api.emergence import router as emergence_router
from app.api.intelligence import router as intelligence_router
from app.api.locations import router as locations_router
from app.api.report_media import router as report_media_router
from app.api.reports import router as reports_router
from app.api.routes import router
from app.core.admission import Admission, AdmissionError
from app.core.config import Settings
from app.core.errors import error_response, http_error_handler, validation_error_handler
from app.core.ingress import IngressMiddleware
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.telemetry import ObservedClient, Telemetry
from app.db.session import create_database_engine
from app.embeddings import Embedder
from app.integrations.storage import create_storage_client
from app.mcp_server import build_mcp


@asynccontextmanager
async def lifespan(application: FastAPI):
    settings = application.state.settings
    async with AsyncExitStack() as cleanup:
        engine = create_database_engine(settings)
        cleanup.push_async_callback(engine.dispose)
        redis = Redis.from_url(
            settings.redis_url.get_secret_value(),
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        cleanup.push_async_callback(redis.aclose)
        storage = create_storage_client(settings)
        cleanup.push_async_callback(asyncio.to_thread, storage.close)
        application.state.db_engine = engine
        application.state.db_sessions = async_sessionmaker(engine, expire_on_commit=False)
        application.state.redis = redis
        if not isinstance(application.state.embedder, ObservedClient):
            application.state.embedder = ObservedClient(
                application.state.embedder, application.state.telemetry, "embedding"
            )
        report_queue = ArqRedis(connection_pool=redis.connection_pool)
        cleanup.push_async_callback(report_queue.aclose)
        application.state.report_queue = report_queue
        application.state.storage = storage
        await cleanup.enter_async_context(application.state.mcp_server.session_manager.run())
        yield


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    configure_logging(settings.log_level)
    application = FastAPI(title=settings.name, version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.embedder = Embedder(settings.embedding_cache_dir)
    application.state.telemetry = Telemetry(lambda: getattr(application.state, "redis", None))
    application.state.admission = Admission(
        settings, lambda: getattr(application.state, "redis", None)
    )
    application.add_middleware(IngressMiddleware, settings=settings)
    application.add_middleware(RequestContextMiddleware, telemetry=application.state.telemetry)

    async def admission_error(request, exc):
        return error_response(
            exc.status,
            "unavailable" if exc.unavailable else "rate_limited",
            "Admission temporarily unavailable; retry safely."
            if exc.unavailable
            else "New-work allowance exceeded; reuse the same inputs and retry later.",
            headers={"Retry-After": "5" if exc.unavailable else "600", "Cache-Control": "no-store"},
        )

    application.add_exception_handler(AdmissionError, admission_error)
    application.add_exception_handler(HTTPException, http_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.include_router(router)
    application.include_router(locations_router)
    application.include_router(intelligence_router)
    application.include_router(emergence_router)
    application.include_router(admin_router)
    application.include_router(admin_page_router)
    application.include_router(reports_router)
    application.include_router(report_media_router)
    mcp_server, transport = build_mcp(application)
    application.state.mcp_server = mcp_server
    # Route the exact endpoint directly: mounting at /mcp would redirect to /mcp/
    # and mounting at / would replace REST's existing not-found/error contract.
    application.router.routes.append(
        Route("/mcp", endpoint=transport, methods=["GET", "POST", "DELETE"])
    )
    return application


app = create_app()
