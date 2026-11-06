"""Shared transactional intake and durable dispatch for REST and MCP."""

from app.db.jobs import dispatch_pending
from app.db.reports import accept_report
from app.schemas.report import ReportReceipt


async def submit_report(session, payload, key, *, storage, bucket, queue):
    async with session.begin():
        report, replayed = await accept_report(
            session, payload, key, storage=storage, bucket=bucket
        )
    queue_pending = False
    if report.processing_job_id is not None:
        try:
            await dispatch_pending(session, queue, job_ids=[report.processing_job_id])
        except Exception:
            # The committed database outbox is retried by the worker.
            queue_pending = True
    return ReportReceipt(
        report_id=report.id,
        status=report.processing_status,
        moderation_status=report.moderation_status,
        moderation_revision=report.moderation_revision,
        replayed=replayed,
        queue_pending=queue_pending,
        message="Report received for review."
        if report.processing_status == "needs_review"
        else "Report received.",
    )
