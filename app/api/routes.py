from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/")
async def root(request: Request) -> dict[str, str]:
    return {"name": request.app.state.settings.name, "version": request.app.version}


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
