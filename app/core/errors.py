from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.core.logging import request_id


def error_response(
    status: int,
    code: str,
    message: str,
    *,
    details: list[dict] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    error = {"code": code, "message": message, "request_id": request_id.get()}
    if details is not None:
        error["details"] = details
    return JSONResponse({"error": error}, status_code=status, headers=headers)


async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return error_response(
        exc.status_code,
        "not_found" if exc.status_code == 404 else "http_error",
        exc.detail if isinstance(exc.detail, str) else "Request failed",
        headers=exc.headers,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Keep field locations/types useful without reflecting submitted values or validator context.
    details = [{"field": list(error["loc"]), "type": error["type"]} for error in exc.errors()]
    return error_response(422, "validation_error", "Request validation failed", details=details)
