from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from app.api.routes import router
from app.core.config import Settings
from app.core.errors import http_error_handler, validation_error_handler
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    configure_logging(settings.log_level)
    application = FastAPI(title=settings.name, version="0.1.0")
    application.state.settings = settings
    application.add_middleware(RequestContextMiddleware)
    application.add_exception_handler(HTTPException, http_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.include_router(router)
    return application


app = create_app()
