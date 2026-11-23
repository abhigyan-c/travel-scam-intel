import asyncio
import hashlib
import hmac
import re
from uuid import uuid4

from sqlalchemy import func, select

from app.db.ingestion import _store_raw
from app.db.reports import _refresh_report_scores
from app.embeddings import text_hash
from app.integrations.fetch import FetchedPage
from app.media import MAX_ATTACHMENTS, MAX_BYTES, MediaError
from app.models import Claim, ReportAttachment, ReportModerationEvent, UserReport


def validate_key(key):
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{15,127}", key) is None:
        raise MediaError("invalid_idempotency_key")


async def authorize_upload(session, report_id, report_key, *, lock=False):
    query = select(UserReport).where(UserReport.id == report_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    report = await session.scalar(query)
    expected = report.submission_key_hash if report is not None else None
    if not hmac.compare_digest(text_hash(report_key), expected or "0" * 64):
        raise MediaError("report_not_found", 404)
    if report is None or expected is None:
        raise MediaError("report_not_found", 404)
    return report


async def add_attachment(
    session, report_id, report_key, upload_key, raw, image, *, storage, bucket
):
    validate_key(upload_key)
    report = await authorize_upload(session, report_id, report_key, lock=True)
    key_hash = text_hash(upload_key)
    existing = await session.scalar(
        select(ReportAttachment).where(
            ReportAttachment.report_id == report.id, ReportAttachment.upload_key_hash == key_hash
        )
    )
    if existing is not None:
        if existing.original_hash != image.original_hash:
            raise MediaError("upload_key_conflict", 409)
        return report, existing, True
    if report.moderation_status == "rejected":
        raise MediaError("report_rejected", 409)
    count = await session.scalar(
        select(func.count())
        .select_from(ReportAttachment)
        .where(ReportAttachment.report_id == report.id)
    )
    if count >= MAX_ATTACHMENTS:
        raise MediaError("attachment_limit", 409)
    base = f"report-media/{report.id}/{key_hash}/{image.original_hash}"
    raw_path, private_path = f"{base}/original", f"{base}/normalized/{image.stored_hash}"
    await asyncio.to_thread(
        _store_raw, storage, bucket, raw_path, FetchedPage("media", raw, image.media_type)
    )
    await asyncio.to_thread(
        _store_raw,
        storage,
        bucket,
        private_path,
        FetchedPage("media", image.content, image.media_type),
    )
    attachment = ReportAttachment(
        id=uuid4(),
        report_id=report.id,
        upload_key_hash=key_hash,
        original_hash=image.original_hash,
        stored_hash=image.stored_hash,
        original_size=len(raw),
        stored_size=len(image.content),
        width=image.width,
        height=image.height,
        media_type=image.media_type,
        raw_object_path=raw_path,
        private_object_path=private_path,
    )
    session.add(attachment)
    previous = report.moderation_status
    text_complete = (
        report.processing_details.get("text_complete", report.processing_details.get("complete"))
        is True
    )
    report.processing_details = {
        **report.processing_details,
        "media_hold": True,
        "text_complete": text_complete,
        "complete": False,
    }
    report.moderation_status = "needs_review"
    if text_complete:
        report.processing_status = "needs_review"
    report.moderation_revision += 1
    session.add(
        ReportModerationEvent(
            report_id=report.id,
            key_hash=text_hash("upload:" + upload_key),
            request_hash=image.original_hash,
            action="attachment_added",
            actor="submitter",
            reason="Private media uploaded",
            previous_status=previous,
            next_status="needs_review",
            revision=report.moderation_revision,
        )
    )
    await session.flush()
    await _refresh_report_scores(session, report.document_id)
    return report, attachment, False


async def moderate_report(session, report_id, decision, key):
    validate_key(key)
    report = await session.scalar(
        select(UserReport).where(UserReport.id == report_id).with_for_update()
    )
    if report is None:
        raise MediaError("report_not_found", 404)
    key_hash = text_hash("moderate:" + key)
    fingerprint = text_hash(decision.model_dump_json())
    event = await session.scalar(
        select(ReportModerationEvent).where(
            ReportModerationEvent.report_id == report_id, ReportModerationEvent.key_hash == key_hash
        )
    )
    if event is not None:
        if event.request_hash != fingerprint:
            raise MediaError("moderation_key_conflict", 409)
        return report, event, True
    if report.moderation_revision != decision.expected_revision:
        raise MediaError("report_revision_changed", 409)
    claims = (
        await session.scalars(
            select(Claim).where(Claim.document_id == report.document_id).order_by(Claim.id)
        )
    ).all()
    attachments = (
        await session.scalars(
            select(ReportAttachment)
            .where(ReportAttachment.report_id == report_id)
            .order_by(ReportAttachment.id)
            .with_for_update()
        )
    ).all()
    if decision.status == "accepted":
        if report.moderation_status == "rejected":
            raise MediaError("reopen_report_before_accepting", 409)
        details = report.processing_details
        if (
            details.get("text_complete", details.get("complete")) is not True
            or details.get("flags")
            or details.get("text_hold")
        ):
            raise MediaError("acceptance_requires_complete_ordinary_text", 409)
        if any(
            claim.structured_data.get("sensitive") is not False
            or claim.structured_data.get("entities")
            or claim.structured_data.get("publication_status") not in {"unreviewed", "approved"}
            or claim.location_id is None
            for claim in claims
        ):
            raise MediaError("sensitive_or_unresolved_release_deferred", 409)
        for attachment in attachments:
            attachment.status = "accepted"
        report.processing_status = "processed"
        report.processing_details = {
            **report.processing_details,
            "media_hold": False,
            "complete": True,
        }
    elif decision.status == "rejected":
        for attachment in attachments:
            attachment.status = "rejected"
    previous = report.moderation_status
    report.moderation_status = decision.status
    if decision.status != "accepted":
        report.processing_status = "needs_review"
        report.processing_details = {**report.processing_details, "complete": False}
    report.moderation_revision += 1
    event = ReportModerationEvent(
        report_id=report_id,
        key_hash=key_hash,
        request_hash=fingerprint,
        action=decision.status,
        actor=decision.actor,
        reason=decision.reason,
        previous_status=previous,
        next_status=decision.status,
        revision=report.moderation_revision,
    )
    session.add(event)
    await session.flush()
    await _refresh_report_scores(session, report.document_id)
    return report, event, False


def read_private_image(storage, bucket, attachment):
    result = storage.get_object(Bucket=bucket, Key=attachment.private_object_path)
    try:
        raw = result["Body"].read(MAX_BYTES + 1)
    finally:
        result["Body"].close()
    if (
        len(raw) != attachment.stored_size
        or hashlib.sha256(raw).hexdigest() != attachment.stored_hash
    ):
        raise MediaError("private_media_integrity_failure", 503)
    return raw
