"""Transactional, replay-safe operator commands over existing domain safeguards."""

import json
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.db.emergence import decide_emergence
from app.db.evidence import invalidate_independence
from app.db.hypothesis_lifecycle import (
    Review,
    apply_resolution,
    merge_hypotheses,
    move_evidence,
    split_hypothesis,
    update_hypothesis,
)
from app.db.jobs import create_job
from app.db.report_media import moderate_report
from app.embeddings import text_hash
from app.media import MediaError
from app.models import (
    AdminAction,
    Document,
    Hypothesis,
    Location,
    LocationResearchState,
    ResearchJob,
    UserReport,
)
from app.schemas.moderation import ModerationDecision


class AdminConflict(ValueError):
    pass


class AdminNotFound(LookupError):
    pass


async def lock_work(session, kind, payload):
    digest = text_hash(str(kind) + json.dumps(payload, sort_keys=True))
    lock = int.from_bytes(bytes.fromhex(digest[:16]), "big", signed=True)
    await session.execute(select(func.pg_advisory_xact_lock(lock)))


async def locked(session, model, identity, expected):
    row = await session.scalar(
        select(model)
        .where(model.id == identity)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise AdminNotFound()
    if row.updated_at != expected:
        raise AdminConflict("stale_record")
    return row


async def command(session, payload, key, *, embedder):
    body = payload.model_dump(mode="json")
    fingerprint = text_hash(json.dumps(body, sort_keys=True, separators=(",", ":")))
    key_hash = text_hash(key)
    identity = await session.scalar(
        insert(AdminAction)
        .values(
            key_hash=key_hash,
            input_hash=fingerprint,
            operation=payload.operation,
            subject_id=payload.subject_id,
            actor="admin-token",
            reason=payload.reason,
            result={},
        )
        .on_conflict_do_nothing(index_elements=[AdminAction.key_hash])
        .returning(AdminAction.id)
    )
    if identity is None:
        previous = await session.scalar(select(AdminAction).where(AdminAction.key_hash == key_hash))
        if previous.input_hash != fingerprint:
            raise AdminConflict("idempotency_conflict")
        return previous, True
    event = await session.get(AdminAction, identity)
    review = Review(actor="admin-token", reason=payload.reason)
    op, subject = payload.operation, payload.subject_id
    domain_key = "admin:" + key_hash
    if op in {"edit_hypothesis", "set_status", "merge", "split", "move_evidence"}:
        # Lock both hypotheses in UUID order before stale checks or lifecycle helpers.
        ids = sorted({subject, *([payload.target_id] if op in {"merge", "move_evidence"} else [])})
        rows = {}
        for item in ids:
            expected = (
                payload.expected_updated_at if item == subject else payload.expected_target_at
            )
            rows[item] = await locked(session, Hypothesis, item, expected)
        row = rows[subject]
        if row.merged_into_id is not None:
            raise AdminConflict("merged_hypothesis")
        if op == "set_status":
            before = row.status.value
            row.status = payload.status
            row.confidence_score = row.prevalence_score = row.recency_score = None
            await invalidate_independence(session, row.id)
            result = {"before": before, "status": payload.status}
        else:
            if op == "edit_hypothesis":
                changed = await update_hypothesis(
                    session, subject, payload.draft, key=domain_key, review=review
                )
            elif op == "merge":
                changed = await merge_hypotheses(
                    session, subject, payload.target_id, key=domain_key, review=review
                )
            elif op == "split":
                changed = await split_hypothesis(
                    session,
                    subject,
                    payload.evidence_ids,
                    draft=payload.draft,
                    key=domain_key,
                    review=review,
                )
            else:
                changed = await move_evidence(
                    session,
                    subject,
                    payload.target_id,
                    payload.evidence_ids,
                    key=domain_key,
                    review=review,
                )
            result = {"lifecycle_event_id": str(changed.id), "target_id": str(changed.target_id)}
    elif op == "apply_resolution":
        changed = await apply_resolution(session, subject, embedder=embedder, review=review)
        result = {"lifecycle_event_id": str(changed.id), "target_id": str(changed.target_id)}
    elif op == "moderate_report":
        try:
            report, moderation, _ = await moderate_report(
                session,
                subject,
                ModerationDecision(
                    status=payload.status,
                    expected_revision=payload.expected_revision,
                    actor="admin-token",
                    reason=payload.reason,
                ),
                domain_key,
            )
        except MediaError as exc:
            raise AdminConflict("moderation_safeguard") from exc
        result = {
            "moderation_event_id": str(moderation.id),
            "status": report.moderation_status,
            "revision": report.moderation_revision,
        }
    elif op == "emerging_decision":
        review_row = await decide_emergence(
            session,
            subject,
            revision=payload.revision,
            action=payload.action,
            reason=payload.reason,
        )
        result = {"status": review_row.status, "revision": review_row.revision}
    elif op == "set_priority":
        location = await session.scalar(
            select(Location).where(Location.id == subject).with_for_update()
        )
        if location is None:
            raise AdminNotFound()
        state = await session.scalar(
            select(LocationResearchState)
            .where(LocationResearchState.location_id == subject)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (state.updated_at if state else None) != payload.expected_updated_at:
            raise AdminConflict("stale_state")
        if state is None:
            state = LocationResearchState(location_id=subject)
            session.add(state)
        before = state.priority_override or 0
        state.priority_override = payload.priority
        result = {"before": before, "priority": payload.priority}
    elif op == "retry_job":
        old = await locked(session, ResearchJob, subject, payload.expected_updated_at)
        if old.status != "failed" or old.kind not in {"process", "report", "ingest"}:
            raise AdminConflict("only_failed_non_research_jobs_can_be_reissued")
        await lock_work(session, old.kind, old.payload)
        # Do not reset a leased job, research-root cursor, reservation or attempt counter.
        if old.kind == "report":
            report = await session.scalar(
                select(UserReport)
                .where(UserReport.id == UUID(old.payload["report_id"]))
                .with_for_update()
            )
            if (
                report is None
                or report.moderation_status in {"rejected", "needs_review"}
                or report.processing_details.get("text_hold")
                or report.processing_details.get("media_hold")
                or report.processing_details.get("flags")
                or report.processing_status not in {"failed", "partial"}
            ):
                raise AdminConflict("report_is_held_or_not_retryable")
        duplicate = await session.scalar(
            select(ResearchJob.id)
            .where(
                ResearchJob.kind == old.kind,
                ResearchJob.payload == old.payload,
                ResearchJob.status.in_(["queued", "running", "retry"]),
            )
            .limit(1)
        )
        if duplicate:
            raise AdminConflict("equivalent_job_active")
        new = await create_job(
            session, kind=old.kind, payload=old.payload, idempotency_key=domain_key
        )
        if old.kind == "report":
            report.processing_job_id = new.id
            report.processing_status = "queued"
            report.moderation_revision += 1
        result = {"previous_job_id": str(old.id), "job_id": str(new.id), "kind": old.kind.value}
    elif op == "reprocess_document":
        row = await locked(session, Document, subject, payload.expected_updated_at)
        report = await session.scalar(
            select(UserReport.id).where(UserReport.document_id == subject)
        )
        if report:
            raise AdminConflict("use_report_retry_for_private_report_documents")
        await lock_work(session, "process", {"document_id": str(subject)})
        active = await session.scalar(
            select(ResearchJob.id)
            .where(
                ResearchJob.kind == "process",
                ResearchJob.payload["document_id"].astext == str(subject),
                ResearchJob.status.in_(["queued", "running", "retry"]),
            )
            .limit(1)
        )
        if active:
            raise AdminConflict("normalization_already_active")
        new = await create_job(
            session,
            kind="process",
            payload={"document_id": str(subject)},
            idempotency_key=domain_key,
        )
        result = {"job_id": str(new.id), "scope": "raw_document_normalization_only"}
    else:
        raise AdminConflict("unknown_command")
    event.result = result
    await session.flush()
    return event, False
