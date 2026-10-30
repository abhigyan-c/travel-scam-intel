import asyncio
import hmac
from typing import Annotated
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response

from app.api.locations import Session
from app.db.report_media import (
    add_attachment,
    authorize_upload,
    moderate_report,
    read_private_image,
)
from app.media import MAX_BYTES, MediaError, validate_image
from app.models import ReportAttachment, UserReport
from app.schemas.moderation import AttachmentReceipt, ModerationDecision, ModerationReceipt

router = APIRouter(tags=["report-media"])
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)]
ReportKey = Annotated[str, Header(alias="Report-Submission-Key", min_length=16, max_length=128)]


async def require_admin(
    request: Request, authorization: Annotated[str | None, Header(max_length=512)] = None
):
    expected = request.app.state.settings.admin_api_token.get_secret_value()
    if len(expected) < 32:
        raise HTTPException(503, "Admin review is not configured")
    supplied = (
        authorization.removeprefix("Bearer ")
        if authorization and authorization.startswith("Bearer ")
        else ""
    )
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(
            401, "Admin authentication required", headers={"WWW-Authenticate": "Bearer"}
        )


@router.post(
    "/v1/reports/{report_id}/attachments",
    status_code=201,
    response_model=AttachmentReceipt,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                kind: {"schema": {"type": "string", "format": "binary"}}
                for kind in ("image/jpeg", "image/png")
            },
        }
    },
)
async def upload_attachment(
    request: Request, report_id: UUID, session: Session, idempotency_key: Key, report_key: ReportKey
):
    try:
        async with session.begin():
            await authorize_upload(session, report_id, report_key)
        if request.headers.get("content-encoding", "identity") != "identity":
            raise MediaError("unsupported_content_encoding", 415)
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_BYTES:
                raise MediaError("invalid_media_size", 413)
            raw.extend(chunk)
        image = await asyncio.to_thread(validate_image, bytes(raw), media_type)
        async with session.begin():
            report, attachment, replayed = await add_attachment(
                session,
                report_id,
                report_key,
                idempotency_key,
                bytes(raw),
                image,
                storage=request.app.state.storage,
                bucket=request.app.state.settings.s3_bucket,
            )
        return AttachmentReceipt(
            attachment_id=attachment.id,
            report_id=report.id,
            status=attachment.status,
            moderation_revision=report.moderation_revision,
            replayed=replayed,
        )
    except MediaError as exc:
        raise HTTPException(exc.status, exc.code) from None
    except (BotoCoreError, ClientError):
        raise HTTPException(
            503, "Private media storage is unavailable; retry with the same key"
        ) from None


@router.post(
    "/v1/admin/reports/{report_id}/moderation",
    response_model=ModerationReceipt,
    dependencies=[Depends(require_admin)],
)
async def review_report(
    report_id: UUID, decision: ModerationDecision, session: Session, idempotency_key: Key
):
    try:
        async with session.begin():
            report, event, replayed = await moderate_report(
                session, report_id, decision, idempotency_key
            )
        return ModerationReceipt(
            event_id=event.id,
            report_id=report.id,
            status=report.moderation_status,
            moderation_revision=report.moderation_revision,
            replayed=replayed,
        )
    except MediaError as exc:
        raise HTTPException(exc.status, exc.code) from None


@router.get("/v1/admin/reports/{report_id}/attachments", dependencies=[Depends(require_admin)])
async def list_private_attachments(report_id: UUID, session: Session, response: Response):
    from sqlalchemy import select

    report = await session.get(UserReport, report_id)
    if report is None:
        raise HTTPException(404, "Report not found")
    response.headers["Cache-Control"] = "no-store"
    rows = (
        await session.scalars(
            select(ReportAttachment)
            .where(ReportAttachment.report_id == report_id)
            .order_by(ReportAttachment.id)
        )
    ).all()
    return {
        "report_id": report_id,
        "status": report.moderation_status,
        "moderation_revision": report.moderation_revision,
        "processing_status": report.processing_status,
        "report_text": report.report_text,
        "text_complete": report.processing_details.get(
            "text_complete", report.processing_details.get("complete")
        )
        is True,
        "flags": report.processing_details.get("flags", []),
        "attachments": [
            {
                "id": row.id,
                "status": row.status,
                "media_type": row.media_type,
                "width": row.width,
                "height": row.height,
            }
            for row in rows
        ],
    }


@router.get(
    "/v1/admin/reports/{report_id}/attachments/{attachment_id}",
    dependencies=[Depends(require_admin)],
)
async def download_private_attachment(
    request: Request, report_id: UUID, attachment_id: UUID, session: Session
):
    attachment = await session.get(ReportAttachment, attachment_id)
    if attachment is None or attachment.report_id != report_id:
        raise HTTPException(404, "Attachment not found")
    try:
        raw = await asyncio.to_thread(
            read_private_image,
            request.app.state.storage,
            request.app.state.settings.s3_bucket,
            attachment,
        )
    except (MediaError, BotoCoreError, ClientError):
        raise HTTPException(503, "Private media is unavailable") from None
    extension = "png" if attachment.media_type == "image/png" else "jpg"
    return Response(
        raw,
        media_type=attachment.media_type,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="attachment-{attachment.id}.{extension}"',
        },
    )
