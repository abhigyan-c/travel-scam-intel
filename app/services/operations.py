"""Numeric private operator projections; never serialize model payloads or raw text."""

from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.models import (
    Claim,
    Document,
    Evidence,
    Hypothesis,
    HypothesisResolution,
    ResearchJob,
    ResearchRun,
    SchedulerCycle,
    SchedulerDailyBudget,
    UserReport,
)


async def operations(session, telemetry):
    now = datetime.now(UTC)
    counts = {}
    for label, column in (
        ("jobs", ResearchJob.status),
        ("documents", Document.processing_status),
        ("claims", Claim.processing_status),
        ("hypotheses", Hypothesis.status),
        ("reports_moderation", UserReport.moderation_status),
        ("reports_processing", UserReport.processing_status),
        ("resolution_decisions", HypothesisResolution.decision),
        ("resolution_review", HypothesisResolution.needs_review),
    ):
        counts[label] = {
            str(key) if key is not None else "unknown": value
            for key, value in (
                await session.execute(select(column, func.count()).group_by(column))
            ).all()
        }
    counts["evidence"] = await session.scalar(select(func.count()).select_from(Evidence))
    created_today = {}
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for label, model in (
        ("claims", Claim),
        ("hypotheses", Hypothesis),
        ("reports", UserReport),
        ("jobs", ResearchJob),
    ):
        created_today[label] = await session.scalar(
            select(func.count()).select_from(model).where(model.created_at >= day_start)
        )
    counts["expired_running_leases"] = await session.scalar(
        select(func.count())
        .select_from(ResearchJob)
        .where(ResearchJob.status == "running", ResearchJob.lease_expires_at <= now)
    )
    counts["scheduler_cycles"] = await session.scalar(
        select(func.count()).select_from(SchedulerCycle)
    )
    budget = await session.get(SchedulerDailyBudget, now.date())
    scheduler_reservations = (
        {field: getattr(budget, field) for field in ("jobs", "queries", "fetches", "llm_calls")}
        if budget
        else None
    )
    # Bound the latency sample, selecting only scalar columns (not report membership/text).
    rows = (
        await session.execute(
            select(
                ResearchRun.status,
                ResearchRun.coverage,
                ResearchRun.created_at,
                ResearchRun.started_at,
                ResearchRun.finished_at,
            )
            .order_by(ResearchRun.created_at.desc().nulls_last(), ResearchRun.id.desc())
            .limit(200)
        )
    ).all()
    elapsed, queue = [], []
    for row in rows:
        if row.created_at and row.finished_at and row.finished_at >= row.created_at:
            elapsed.append((row.finished_at - row.created_at).total_seconds())
        if row.created_at and row.started_at and row.started_at >= row.created_at:
            queue.append((row.started_at - row.created_at).total_seconds())

    def distribution(values):
        values = sorted(values)
        return dict(
            count=len(values),
            mean_seconds=sum(values) / len(values) if values else None,
            max_seconds=max(values) if values else None,
        )

    return dict(
        as_of=datetime.now(UTC).isoformat(),
        counters=await telemetry.snapshot(),
        persisted_counts=counts,
        created_today_utc=created_today,
        scheduler_reserved_today=scheduler_reservations,
        research_sample=dict(
            limit=200,
            rows=len(rows),
            order="newest_created_first",
            status=dict(Counter(row.status for row in rows)),
            coverage=dict(Counter(row.coverage for row in rows)),
            end_to_end=distribution(elapsed),
            queue_wait=distribution(queue),
        ),
    )
