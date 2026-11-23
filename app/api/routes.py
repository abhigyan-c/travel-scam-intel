from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.readiness import check_dependencies

router = APIRouter()


@router.get("/")
async def root(request: Request) -> dict[str, str]:
    return {"name": request.app.state.settings.name, "version": request.app.version}


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", responses={503: {"description": "A required dependency is unavailable"}})
async def ready(request: Request) -> JSONResponse:
    state = request.app.state
    checks = await check_dependencies(
        state.db_engine,
        state.redis,
        state.storage,
        state.settings.s3_bucket,
        state.settings.readiness_timeout_seconds,
    )
    is_ready = all(value == "ok" for value in checks.values())
    return JSONResponse(
        {"status": "ready" if is_ready else "not_ready", "checks": checks},
        status_code=200 if is_ready else 503,
    )
