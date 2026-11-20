"""Canonical root metadata; called only inside existing fenced writer transactions."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from app.db.hypothesis_resolution import _scope
from app.db.resolution import get_location_path
from app.models import Claim, Evidence, Hypothesis, ResearchRun
from app.services.public_evidence import current_hypothesis, eligible, join_evidence


async def public_observations(session, run, identities):
    result = {}
    # Query only recorded checks, not an unbounded historical evidence cohort.
    recorded = sorted(
        (checked, document)
        for pattern in identities
        for document, checked in run.observations.get(str(pattern), {}).items()
    )
    documents = list({UUID(document) for _, document in recorded[-10000:]})
    if not documents:
        return result
    rows = await session.execute(
        join_evidence(select(Evidence.hypothesis_id, Claim.document_id))
        .join(Hypothesis, Hypothesis.id == Evidence.hypothesis_id)
        .where(
            Evidence.hypothesis_id.in_(identities),
            Claim.document_id.in_(documents),
            Evidence.stance == "supports",
            eligible(),
        )
    )
    for pattern, document in rows:
        checks = run.observations.get(str(pattern), {})
        checked = checks.get(str(document)) if isinstance(checks, dict) else None
        if checked and (str(pattern) not in result or checked > result[str(pattern)]):
            result[str(pattern)] = checked
    return result


async def fresh_run(session, run, now, seconds):
    if run is None or run.status != "completed":
        return False
    if not run.pattern_ids:
        return bool(
            run.last_completed_at and run.last_completed_at + timedelta(seconds=seconds) > now
        )
    current = await public_observations(session, run, [UUID(i) for i in run.pattern_ids])
    observations = [current.get(i) for i in run.pattern_ids]
    if not all(observations):
        return False
    return min(datetime.fromisoformat(t) for t in observations) + timedelta(seconds=seconds) > now


async def new_run(session, state):
    path = await get_location_path(session, state.location_id)
    ids = list(
        await session.scalars(
            select(Hypothesis.id)
            .where(_scope(state.location_id, path), current_hypothesis())
            .order_by(Hypothesis.id)
            .limit(1000)
        )
    )
    prior = await session.get(ResearchRun, state.current_run_id) if state.current_run_id else None
    run = ResearchRun(
        location_id=state.location_id,
        created_at=datetime.now(UTC),
        pattern_ids=[str(i) for i in ids],
        coverage=prior.coverage if prior else "unknown",
        observations=dict(prior.observations) if prior else {},
        last_result_update_at=prior.last_result_update_at if prior else None,
        last_completed_at=prior.last_completed_at if prior else None,
    )
    session.add(run)
    await session.flush()
    state.current_run_id = run.id
    return run


async def observe(session, job, cursor, claim_id, result):
    if not job.report_run_id or not result.get("hypothesis_ids"):
        return
    run = await session.get(ResearchRun, job.report_run_id)
    claim = await session.get(Claim, claim_id)
    observed = next(
        (
            c.get("fetched_at")
            for c in cursor.candidates.values()
            if c.get("document_id") == str(claim.document_id) and c.get("fetched_at")
        ),
        None,
    )
    ids = list(dict.fromkeys([*result["hypothesis_ids"], *run.pattern_ids]))[:1000]
    if run.coverage == "unknown" or any(i not in run.pattern_ids for i in result["hypothesis_ids"]):
        run.coverage = "partial"
    observations = dict(run.observations)
    if observed:
        # Only successful ingestion records this checkpoint; retries, reads and an
        # immutable document's old retrieved_at cannot rejuvenate source evidence.
        observations = {
            **run.observations,
            **{
                i: {**run.observations.get(i, {}), str(claim.document_id): observed}
                for i in result["hypothesis_ids"]
            },
        }
        observations = {
            p: dict(sorted(checks.items(), key=lambda x: x[1])[-64:])
            for p, checks in observations.items()
        }
    if ids != run.pattern_ids or observations != run.observations:
        run.last_result_update_at = datetime.now(UTC)
    run.pattern_ids, run.observations = ids, observations


def reasons(summary):
    codes = {e.get("code") for e in summary.get("errors", [])}
    codes.update([summary.get("error_code"), summary.get("stop_reason")])
    result = []
    if codes & {"deadline", "run_time_limit"}:
        result.append("time_limit")
    if codes & {
        "resource_limit",
        "run_llm_limit",
        "request_limit",
        "pipeline_headroom",
    }:
        result.append("resource_limit")
    if codes & {"http_status", "empty", "host_deferred", "run_fetch_limit"}:
        result.append("source_failures")
    if codes & {"semantic_provider_failed", "rate_limited", "provider_unavailable"}:
        result.append("provider_unavailable")
    if codes & {"review_required", "claim_not_public"}:
        result.append("review_pending")
    if not summary.get("complete") and not result:
        result.append("processing_incomplete")
    return sorted(set(result))


async def finish_run(session, job, summary, *, terminal):
    if not job.report_run_id:
        return
    run = await session.get(ResearchRun, job.report_run_id)
    if not terminal:
        run.status = "queued"
        return
    run.finished_at = datetime.now(UTC)
    complete = bool(summary.get("complete"))
    retained_progress = bool(
        summary.get("claim_ids")
        or summary.get("documents")
        or (
            run.created_at
            and run.last_result_update_at
            and run.last_result_update_at >= run.created_at
        )
    )
    run.status = (
        "completed"
        if complete
        else "failed"
        if summary.get("status") == "failed" and not retained_progress
        else "partial"
    )
    run.stop_reasons = [] if complete else reasons(summary)
    if complete:
        run.coverage, run.last_completed_at = "complete", run.finished_at
    elif retained_progress:
        run.coverage = "partial"
