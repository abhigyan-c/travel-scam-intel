from typing import Annotated

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Header, HTTPException, Request

from app.api.locations import Session
from app.db.jobs import dispatch_pending
from app.db.reports import accept_report
from app.reports import InvalidReport, ReportConflict
from app.schemas.report import ReportCreate, ReportReceipt

router = APIRouter(prefix="/v1/reports", tags=["reports"])


@router.post("", status_code=202, response_model=ReportReceipt)
async def submit_report(
    request: Request,
    payload: ReportCreate,
    session: Session,
    idempotency_key: Annotated[str, Header(min_length=16, max_length=128)],
):
    try:
        async with session.begin():
            report, replayed = await accept_report(
                session,
                payload,
                idempotency_key,
                storage=request.app.state.storage,
                bucket=request.app.state.settings.s3_bucket,
            )
    except ReportConflict:
        raise HTTPException(
            409, "Idempotency key was already used for different report content"
        ) from None
    except (InvalidReport, ValueError):
        raise HTTPException(422, "Invalid report or location context") from None
    except (BotoCoreError, ClientError):
        raise HTTPException(503, "Report storage is unavailable; retry with the same key") from None
    queue_pending = False
    if report.processing_job_id is not None:
        try:
            await dispatch_pending(
                session, request.app.state.report_queue, job_ids=[report.processing_job_id]
            )
        except Exception:
            queue_pending = True
    return ReportReceipt(
        report_id=report.id,
        status=report.processing_status,
        replayed=replayed,
        queue_pending=queue_pending,
        message="Report received for review."
        if report.processing_status == "needs_review"
        else "Report received.",
    )
