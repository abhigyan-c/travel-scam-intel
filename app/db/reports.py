"""Transactional report intake and processing through shared intelligence helpers."""

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.db.claim_pipeline import apply_and_score_claim, embed_hypothesis
from app.db.extraction import extract_document
from app.db.frequency import calculate_recency_and_frequency
from app.db.ingestion import _store_raw
from app.db.jobs import create_job
from app.db.resolution import get_location_path, resolve_claim, resolve_location
from app.embeddings import text_hash
from app.integrations.fetch import FetchedPage
from app.models import (
    Claim,
    Document,
    Evidence,
    ReportAttachment,
    ReportModerationEvent,
    Source,
    UserReport,
)
from app.models.research_job import JobKind
from app.processing import PROCESSING_VERSION, process_bytes
from app.relevance import PROMPT_VERSION, RelevanceResult, classify_text
from app.reports import VERSION, InvalidReport, ReportConflict, prepare_text, redact_pii
from app.research import _LimitedClient


async def accept_report(session, payload, key, *, storage, bucket, admission=None):
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{15,127}", key) is None:
        raise InvalidReport("invalid_idempotency_key")
    raw = json.dumps(
        payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    fingerprint, key_hash = hashlib.sha256(raw).hexdigest(), text_hash(key)
    lock = int.from_bytes(bytes.fromhex(key_hash[:16]), "big", signed=True)
    await session.execute(select(func.pg_advisory_xact_lock(lock)))
    existing = await session.scalar(
        select(UserReport).where(UserReport.submission_key_hash == key_hash)
    )
    if existing is not None:
        if existing.submission_hash != fingerprint:
            raise ReportConflict("idempotency_payload_conflict")
        return existing, True
    clean, flags = prepare_text(payload.report_text)
    if admission is not None:
        await admission.require("reports", key_hash)
    processed = await asyncio.to_thread(process_bytes, clean.encode(), content_type="text/plain")
    if processed.language != "en":
        flags.append("english_not_confirmed")
    if payload.occurred_at is not None and payload.occurred_at > datetime.now(UTC):
        flags.append("future_occurrence_date")
    location_id, location_resolution = payload.location_id, None
    if location_id is not None:
        await get_location_path(session, location_id)
    elif payload.location_text is not None:
        _, context_flags = redact_pii(payload.location_text)
        if context_flags:
            flags.append("location_pii_requires_review")
        else:
            location_resolution = await resolve_location(
                session,
                payload.location_text,
                parent_id=payload.parent_id,
                country_code=payload.country_code,
                location_type=payload.location_type,
                create_provisional=not flags,
            )
            location_id = location_resolution.location_id
            if location_id is None:
                flags.append("location_requires_review")
    path = f"reports/{key_hash}/{fingerprint}"
    await asyncio.to_thread(
        _store_raw, storage, bucket, path, FetchedPage("report", raw, "application/json")
    )
    source = Source(platform="user_report", source_type="user_report")
    session.add(source)
    await session.flush()
    document = Document(
        source_id=source.id,
        raw_object_path=path,
        content_hash=fingerprint,
        clean_text=processed.text or clean,
        clean_text_hash=text_hash(processed.text or clean),
        language=processed.language,
        processing_status="processed",
        document_metadata={
            "submission": {"version": VERSION, "flags": flags},
            "processing": {
                "version": PROCESSING_VERSION,
                "outcome": processed.outcome,
                "eligible_for_extraction": processed.language == "en" and not flags,
            },
        },
    )
    session.add(document)
    await session.flush()
    report = UserReport(
        id=uuid4(),
        document_id=document.id,
        location_id=location_id,
        report_text=processed.text or clean,
        provenance_class=payload.provenance_class,
        occurred_at=payload.occurred_at,
        submission_key_hash=key_hash,
        submission_hash=fingerprint,
        moderation_status="needs_review" if flags else "pending",
        processing_status="needs_review" if flags else "queued",
        processing_details={
            "version": VERSION,
            "flags": flags,
            "location_resolution": location_resolution.model_dump(mode="json")
            if location_resolution
            else None,
        },
    )
    session.add(report)
    await session.flush()
    if not flags:
        job = await create_job(
            session,
            kind=JobKind.REPORT,
            payload={"report_id": str(report.id)},
            idempotency_key=f"report:{report.id}",
        )
        report.processing_job_id = job.id
        await session.flush()
    return report, False


async def _refresh_report_scores(session, document_id):
    identities = (
        await session.scalars(
            select(Evidence.hypothesis_id)
            .join(Claim, Evidence.claim_id == Claim.id)
            .where(Claim.document_id == document_id, Evidence.archived_at.is_(None))
            .distinct()
        )
    ).all()
    for identity in sorted(identities):
        await calculate_recency_and_frequency(session, identity)


async def process_report(session, report_id: UUID, *, client, embedder):
    report = await session.scalar(
        select(UserReport).where(UserReport.id == report_id).with_for_update()
    )
    if report is None:
        raise ValueError("report_not_found")
    details = report.processing_details
    media_only = (
        bool(details.get("media_hold"))
        and not details.get("flags")
        and not details.get("text_hold")
    )
    if (
        report.processing_status == "processed"
        or report.moderation_status == "rejected"
        or (
            report.processing_status == "needs_review"
            and not (media_only and not details.get("text_complete"))
        )
        or (report.moderation_status == "needs_review" and not media_only)
    ):
        return {"status": report.processing_status, "retry": False}
    previous_status = report.moderation_status
    media_pending = (
        await session.scalar(
            select(ReportAttachment.id)
            .where(
                ReportAttachment.report_id == report.id, ReportAttachment.status == "needs_review"
            )
            .limit(1)
        )
        is not None
    )
    document = await session.get(Document, report.document_id)
    deadline = asyncio.get_running_loop().time() + 40
    budget = _LimitedClient(client, 8, concurrency=1)
    cached = document.document_metadata.get("relevance")
    relevance = RelevanceResult.model_validate(cached) if cached else None
    if (
        relevance is None
        or not relevance.complete
        or relevance.text_hash != document.clean_text_hash
        or relevance.prompt_version != PROMPT_VERSION
    ):
        relevance = await classify_text(document.clean_text, budget, max_passages=3, timeout=12)
        if relevance.complete:
            document.document_metadata = {
                **document.document_metadata,
                "relevance": relevance.model_dump(mode="json"),
            }
    extraction = await extract_document(
        session, document.id, relevance, budget, max_passages=3, timeout=20
    )
    claims = (
        await session.scalars(
            select(Claim).where(Claim.document_id == document.id).order_by(Claim.id)
        )
    ).all()
    held = False
    for claim in claims:
        metadata = claim.structured_data
        held |= bool(metadata.get("sensitive") or metadata.get("entities"))
        if report.location_id is not None and not metadata.get(
            "unresolved_location", metadata.get("location")
        ):
            claim.location_id = report.location_id
            claim.structured_data = {
                **metadata,
                "location_resolution": {
                    "method": "submission_context",
                    "location_id": str(report.location_id),
                },
            }
        else:
            await resolve_claim(
                session, claim.id, parent_id=report.location_id, create_provisional=True
            )
            await session.refresh(claim)
        held |= claim.location_id is None
    report.moderation_status = (
        "needs_review"
        if held or media_pending
        else "processed"
        if claims or extraction.complete
        else "pending"
    )
    report.processing_status = (
        "needs_review" if held else "processed" if extraction.complete else "partial"
    )
    await session.flush()
    outcomes = []
    text_hold = held
    processing_errors = set()
    retry = not extraction.complete
    if not held:
        for claim in claims:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                retry = True
                processing_errors.add("deadline")
                break
            try:
                async with session.begin_nested(), asyncio.timeout(remaining):
                    outcome = await apply_and_score_claim(
                        session, claim.id, embedder=embedder, client=budget
                    )
                outcomes.append(outcome)
                for identity in outcome.get("hypothesis_ids", []):
                    async with (
                        session.begin_nested(),
                        asyncio.timeout(max(0.01, deadline - asyncio.get_running_loop().time())),
                    ):
                        embedded = await embed_hypothesis(
                            session, UUID(identity), embedder=embedder
                        )
                        if embedded["error"]:
                            processing_errors.add(embedded["error"])
                            retry = True
            except (TimeoutError, ValueError, RuntimeError, OSError) as exc:
                processing_errors.add(
                    "deadline" if isinstance(exc, TimeoutError) else "application_incomplete"
                )
                retry = True
                break
        if any(outcome.get("pending_resolution_ids") for outcome in outcomes):
            # Resolver failures are retryable; actual semantic/ambiguity decisions need review.
            pending_review = any(outcome.get("error") == "review_required" for outcome in outcomes)
            retry |= not pending_review
            report.processing_status = "needs_review" if pending_review else "partial"
            if pending_review:
                report.moderation_status = "needs_review"
                text_hold = True
        if retry and report.processing_status != "needs_review":
            report.processing_status = "partial"
    if text_hold:
        for claim in claims:
            claim.structured_data = {**claim.structured_data, "publication_status": "held"}
    text_complete = extraction.complete and not retry and not text_hold
    if media_pending and text_complete:
        report.processing_status = "needs_review"
    await session.flush()
    await _refresh_report_scores(session, document.id)
    report.processing_details = {
        **report.processing_details,
        "complete": report.processing_status == "processed",
        "text_complete": text_complete,
        "text_hold": text_hold,
        "claim_count": len(claims),
        "extraction_complete": extraction.complete,
        "error_codes": sorted(
            {row["error"] for row in extraction.attempts if row.get("error")}
            | {row["error"] for row in outcomes if row.get("error")}
            | processing_errors
            | {row["error"] for row in budget.usage if row.get("error")}
        ),
        "usage": {"llm_calls": budget.calls, "llm": budget.usage},
    }
    report.moderation_revision += 1
    session.add(
        ReportModerationEvent(
            report_id=report.id,
            key_hash=text_hash(f"processing:{report.id}:{report.moderation_revision}"),
            request_hash=text_hash(
                f"{document.clean_text_hash}:{report.moderation_revision}:{report.moderation_status}"
            ),
            action="processing_completed",
            actor="worker",
            reason="Report text processing attempt completed",
            previous_status=previous_status,
            next_status=report.moderation_status,
            revision=report.moderation_revision,
        )
    )
    await session.flush()
    return {"status": report.processing_status, "retry": report.processing_status == "partial"}
