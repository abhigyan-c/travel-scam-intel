from typing import Annotated

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Header, HTTPException, Request

from app.api.locations import Session
from app.reports import InvalidReport, ReportConflict
from app.schemas.public_api import ErrorEnvelope
from app.schemas.report import ReportCreate, ReportReceipt
from app.services.report_intake import submit_report as intake_report

router = APIRouter(prefix="/v1/reports", tags=["reports"])


@router.post(
    "",
    status_code=202,
    response_model=ReportReceipt,
    summary="Submit an anonymous traveler report",
    description="Stores a normalized report and queues processing. "
    "Retry with the same Idempotency-Key and content to retrieve the same receipt.",
    responses={
        409: {
            "model": ErrorEnvelope,
            "description": "Idempotency key reused for different content.",
        },
        422: {
            "model": ErrorEnvelope,
            "description": "Invalid report, location context or request.",
        },
        503: {"model": ErrorEnvelope, "description": "Report storage temporarily unavailable."},
    },
)
async def submit_report(
    request: Request,
    payload: ReportCreate,
    session: Session,
    idempotency_key: Annotated[str, Header(min_length=16, max_length=128)],
):
    try:
        return await intake_report(
            session,
            payload,
            idempotency_key,
            storage=request.app.state.storage,
            bucket=request.app.state.settings.s3_bucket,
            queue=request.app.state.report_queue,
            admission=request.app.state.admission,
        )
    except ReportConflict:
        raise HTTPException(
            409, "Idempotency key was already used for different report content"
        ) from None
    except (InvalidReport, ValueError):
        raise HTTPException(422, "Invalid report or location context") from None
    except (BotoCoreError, ClientError):
        raise HTTPException(503, "Report storage is unavailable; retry with the same key") from None
