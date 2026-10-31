import asyncio
from contextlib import AsyncExitStack, asynccontextmanager

from arq.connections import ArqRedis
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.exceptions import HTTPException

from app.api.intelligence import router as intelligence_router
from app.api.locations import router as locations_router
from app.api.report_media import router as report_media_router
from app.api.reports import router as reports_router
from app.api.routes import router
from app.core.config import Settings
from app.core.errors import http_error_handler, validation_error_handler
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db.session import create_database_engine
from app.embeddings import Embedder
from app.integrations.storage import create_storage_client


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
        report_queue = ArqRedis(connection_pool=redis.connection_pool)
        cleanup.push_async_callback(report_queue.aclose)
        application.state.report_queue = report_queue
        application.state.storage = storage
        yield


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    configure_logging(settings.log_level)
    application = FastAPI(title=settings.name, version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.embedder = Embedder(settings.embedding_cache_dir)
    application.add_middleware(RequestContextMiddleware)
    application.add_exception_handler(HTTPException, http_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.include_router(router)
    application.include_router(locations_router)
    application.include_router(intelligence_router)
    application.include_router(reports_router)
    application.include_router(report_media_router)
    return application


app = create_app()
