"""Cheap bounded selection, durable reservations and Redis outbox delivery."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import case, delete, func, or_, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert

from app.db.jobs import create_job, dispatch_pending
from app.frequency import utc
from app.models import (
    Claim,
    Evidence,
    Hypothesis,
    HypothesisResearchState,
    Location,
    LocationDemandHour,
    LocationResearchState,
    ResearchJob,
    SchedulerCycle,
    SchedulerDailyBudget,
    SchedulerReservation,
    Source,
)
from app.models.research_job import JobKind, JobStatus
from app.research import ResearchLeaseLost, ResearchLimits
from app.scheduling import fits, priority, reservation
from app.services.public_evidence import eligible, join_evidence

ACTIVE = {JobStatus.QUEUED, JobStatus.RETRY, JobStatus.RUNNING}


class SchedulerBudgetDeferred(ResearchLeaseLost):
    pass


class _BudgetGuard:
    def __init__(self, ctx, identity, delegate, token):
        self.ctx, self.identity, self.delegate = ctx, identity, delegate
        self.token = token

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def _call(self, method, *args, **kwargs):
        if not await ensure_execution_budget(self.ctx, self.identity):
            raise SchedulerBudgetDeferred()
        if self.token is not None:
            async with self.ctx["sessions"]() as session:
                owned = await session.scalar(
                    select(ResearchJob.id).where(
                        ResearchJob.id == self.identity,
                        ResearchJob.run_token == self.token,
                        ResearchJob.status == JobStatus.RUNNING,
                        ResearchJob.lease_expires_at > datetime.now(UTC),
                    )
                )
            if owned is None:
                raise ResearchLeaseLost()
        return await getattr(self.delegate, method)(*args, **kwargs)

    async def search(self, *args, **kwargs):
        return await self._call("search", *args, **kwargs)

    async def fetch(self, *args, **kwargs):
        return await self._call("fetch", *args, **kwargs)

    async def complete(self, *args, **kwargs):
        return await self._call("complete", *args, **kwargs)


def guarded_context(ctx, identity, *, token=None):
    return {
        **ctx,
        **{
            name: _BudgetGuard(ctx, identity, ctx[name], token)
            for name in ("provider", "fetcher", "llm")
        },
    }


async def _lock(session):
    # One transaction-scoped lock for admission and UTC-day renewal, across all workers.
    await session.execute(text("SELECT pg_advisory_xact_lock(730030)"))


async def _daily(session, day):
    await session.execute(insert(SchedulerDailyBudget).values(day=day).on_conflict_do_nothing())
    return await session.get(SchedulerDailyBudget, day, populate_existing=True)


def _usage(row):
    return {key: getattr(row, key) for key in ("jobs", "queries", "fetches", "llm_calls")}


async def _charge(session, job, daily, limits):
    for key, value in reservation(limits).items():
        setattr(daily, key, getattr(daily, key) + value)
    session.add(SchedulerReservation(job_id=job.id, day=daily.day))
    await session.flush()


async def ensure_execution_budget(ctx, identity, *, as_of=None):
    now = utc(as_of or datetime.now(UTC))
    async with ctx["sessions"]() as session, session.begin():
        job = await session.get(ResearchJob, identity)
        if job is None:
            return False
        if "scheduler_limits" not in job.payload:
            return True
        if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED}:
            return False
        if not ctx["settings"].scheduler.enabled:
            return False
        if await session.get(SchedulerReservation, (identity, now.date())):
            return True
        await _lock(session)
        if await session.get(SchedulerReservation, (identity, now.date())):
            return True
        limits = ResearchLimits.model_validate(job.payload["scheduler_limits"])
        daily = await _daily(session, now.date())
        if not fits(_usage(daily), reservation(limits), ctx["settings"].scheduler.daily):
            return False
        await _charge(session, job, daily, limits)
        return True


def _light_eligible():
    # Scheduling hints use cached grades and metadata, never full-text hashing or scoring.
    # Workers revalidate actual public support before spending resources on a hypothesis.
    return (
        eligible(verify_document=False),
        Evidence.weight_run_id.is_not(None),
        func.jsonb_typeof(Evidence.weight_components["public_eligible"]) == "boolean",
        Evidence.weight_components["public_eligible"].astext == "true",
        Hypothesis.status.in_(["active", "candidate", "disputed"]),
        Hypothesis.merged_into_id.is_(None),
    )


async def _candidates(session, policy, now):
    hour = now.replace(minute=0, second=0, microsecond=0)
    demand = (
        select(
            LocationDemandHour.location_id,
            func.coalesce(
                func.sum(LocationDemandHour.requests).filter(
                    LocationDemandHour.hour > hour - timedelta(hours=24)
                ),
                0,
            ).label("d24"),
            func.coalesce(
                func.sum(LocationDemandHour.requests).filter(
                    LocationDemandHour.hour > hour - timedelta(days=7)
                ),
                0,
            ).label("d7"),
            func.coalesce(
                func.sum(LocationDemandHour.requests).filter(
                    LocationDemandHour.hour > hour - timedelta(days=30)
                ),
                0,
            ).label("d30"),
            func.max(LocationDemandHour.hour).label("last_demand"),
        )
        .where(
            LocationDemandHour.hour <= hour,
            LocationDemandHour.hour > hour - timedelta(days=policy.dormant_days),
        )
        .group_by(LocationDemandHour.location_id)
        .subquery()
    )
    evidence = (
        join_evidence(
            select(
                Hypothesis.id.label("hypothesis_id"),
                Hypothesis.primary_location_id.label("location_id"),
                func.count(func.distinct(Source.id))
                .filter(Claim.created_at > now - timedelta(days=7))
                .label("activity"),
                func.count().filter(Evidence.stance == "contradicts").label("contradictions"),
            )
        )
        .join(Hypothesis, Hypothesis.id == Evidence.hypothesis_id)
        .where(*_light_eligible(), Claim.created_at <= now)
        .group_by(Hypothesis.id, Hypothesis.primary_location_id)
        .subquery()
    )
    location_activity = (
        join_evidence(
            select(
                Hypothesis.primary_location_id.label("location_id"),
                func.count(func.distinct(Source.id))
                .filter(Claim.created_at > now - timedelta(days=7))
                .label("activity"),
                func.max(case((Evidence.stance == "contradicts", 1), else_=0)).label("uncertain"),
            )
        )
        .join(Hypothesis, Hypothesis.id == Evidence.hypothesis_id)
        .where(*_light_eligible(), Claim.created_at <= now)
        .group_by(Hypothesis.primary_location_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(
                Location,
                LocationResearchState,
                func.coalesce(demand.c.d24, 0).label("d24"),
                func.coalesce(demand.c.d7, 0).label("d7"),
                func.coalesce(demand.c.d30, 0).label("d30"),
                demand.c.last_demand,
                func.coalesce(location_activity.c.activity, 0).label("activity"),
                location_activity.c.uncertain,
            )
            .outerjoin(LocationResearchState, LocationResearchState.location_id == Location.id)
            .outerjoin(demand, demand.c.location_id == Location.id)
            .outerjoin(location_activity, location_activity.c.location_id == Location.id)
            .where(or_(demand.c.location_id.is_not(None), location_activity.c.activity > 0))
            .order_by(
                func.coalesce(LocationResearchState.priority_override, 0).desc(),
                func.coalesce(demand.c.d24, 0).desc(),
                func.coalesce(location_activity.c.activity, 0).desc(),
                Location.id,
            )
            .limit(policy.candidate_limit + 1)
        )
    ).all()
    truncated = len(rows) > policy.candidate_limit
    result = []
    for row in rows[: policy.candidate_limit]:
        state = row.LocationResearchState
        job = (
            await session.get(ResearchJob, state.active_job_id)
            if state and state.active_job_id
            else None
        )
        first = state is None or state.enriched_at is None
        dates = [state.enriched_at, state.last_success_at] if state and not first else []
        if job and job.payload.get("phase") != "initial":
            dates.append(job.created_at)
        last = max((value for value in dates if value is not None), default=None)
        result.append(
            {
                "kind": "location",
                "id": row.Location.id,
                "location_id": row.Location.id,
                "d24": row.d24,
                "d7": row.d7,
                "d30": row.d30,
                "last_demand": row.last_demand,
                "activity": row.activity,
                "uncertain": bool(row.uncertain),
                "last_run": last,
                "next_retry": state.next_retry_at if state else None,
                "busy": bool(state and state.status == "researching"),
                "incomplete": bool(state is None or state.progress.get("complete") is not True),
                "first_deep": first,
                "new_hypothesis": False,
                "priority_adjustment": state.priority_override if state else 0,
            }
        )
    hyps = (
        await session.execute(
            select(
                Hypothesis,
                HypothesisResearchState,
                func.coalesce(demand.c.d24, 0).label("d24"),
                func.coalesce(demand.c.d7, 0).label("d7"),
                func.coalesce(demand.c.d30, 0).label("d30"),
                demand.c.last_demand,
                evidence.c.activity,
                evidence.c.contradictions,
            )
            .join(evidence, evidence.c.hypothesis_id == Hypothesis.id)
            .outerjoin(
                HypothesisResearchState, HypothesisResearchState.hypothesis_id == Hypothesis.id
            )
            .outerjoin(demand, demand.c.location_id == Hypothesis.primary_location_id)
            .where(or_(demand.c.d30 > 0, evidence.c.activity > 0))
            .order_by(
                func.coalesce(demand.c.d24, 0).desc(), evidence.c.activity.desc(), Hypothesis.id
            )
            .limit(policy.candidate_limit + 1)
        )
    ).all()
    truncated = truncated or len(hyps) > policy.candidate_limit
    for row in hyps[: policy.candidate_limit]:
        h, state = row.Hypothesis, row.HypothesisResearchState
        job = (
            await session.get(ResearchJob, state.active_job_id)
            if state and state.active_job_id
            else None
        )
        result.append(
            {
                "kind": "hypothesis",
                "id": h.id,
                "location_id": h.primary_location_id,
                "d24": row.d24,
                "d7": row.d7,
                "d30": row.d30,
                "last_demand": row.last_demand,
                "activity": row.activity,
                "uncertain": bool(
                    row.contradictions
                    or h.confidence_score is None
                    or h.confidence_score < 0.5
                    or h.status == "disputed"
                ),
                "last_run": state.last_attempt_at if state else None,
                "next_retry": state.next_retry_at if state else None,
                "busy": bool(job and job.status in ACTIVE),
                "incomplete": state is None or state.last_success_at is None,
                "first_deep": False,
                "new_hypothesis": h.created_at > now - timedelta(days=7),
            }
        )
    return result, truncated


async def scheduler_cycle(ctx, *, as_of=None):
    from app.on_demand import _new_job, limits_for

    now = utc(as_of or datetime.now(UTC))
    policy = ctx["settings"].scheduler
    selected, used = [], {key: 0 for key in policy.cycle.model_dump()}
    async with ctx["sessions"]() as session, session.begin():
        # Bounded privacy retention also runs when proactive scheduling is disabled.
        expired = (
            select(LocationDemandHour.location_id, LocationDemandHour.hour)
            .where(LocationDemandHour.hour < now - timedelta(days=policy.dormant_days))
            .limit(10000)
        )
        await session.execute(
            delete(LocationDemandHour).where(
                tuple_(LocationDemandHour.location_id, LocationDemandHour.hour).in_(expired)
            )
        )
        if not policy.enabled:
            return {"enabled": False, "selected": [], "coverage_complete": True}
        await _lock(session)
        slot = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        existing = await session.get(SchedulerCycle, slot)
        if existing is not None:
            return {**existing.summary, "replayed": True}
        daily = await _daily(session, now.date())
        candidates, truncated = await _candidates(session, policy, now)
        ranked = [
            value for item in candidates if (value := priority(item, policy, now)) is not None
        ]
        ranked.sort(key=lambda item: (-item["priority"], item["kind"], str(item["id"])))
        places = set()
        for item in ranked:
            if item["location_id"] in places:
                continue
            limits = limits_for(ctx["settings"], "enrichment")
            cost = reservation(limits)
            if not fits(used, cost, policy.cycle) or not fits(_usage(daily), cost, policy.daily):
                continue
            await session.execute(
                insert(LocationResearchState)
                .values(location_id=item["location_id"])
                .on_conflict_do_nothing()
            )
            location = await session.scalar(
                select(LocationResearchState)
                .where(LocationResearchState.location_id == item["location_id"])
                .with_for_update()
            )
            if location.status == "researching":
                continue
            busy = await session.scalar(
                select(HypothesisResearchState.hypothesis_id)
                .join(Hypothesis, Hypothesis.id == HypothesisResearchState.hypothesis_id)
                .join(ResearchJob, ResearchJob.id == HypothesisResearchState.active_job_id)
                .where(
                    Hypothesis.primary_location_id == item["location_id"],
                    ResearchJob.status.in_(ACTIVE),
                )
                .limit(1)
            )
            if busy:
                continue
            if item["kind"] == "location":
                job = await _new_job(
                    session,
                    location,
                    "enrichment" if item["first_deep"] else "refresh",
                    scheduler_limits=limits,
                )
            else:
                await session.execute(
                    insert(HypothesisResearchState)
                    .values(hypothesis_id=item["id"])
                    .on_conflict_do_nothing()
                )
                state = await session.get(
                    HypothesisResearchState, item["id"], populate_existing=True
                )
                job = await create_job(
                    session,
                    kind=JobKind.HYPOTHESIS_RESEARCH,
                    payload={
                        "hypothesis_id": str(item["id"]),
                        "scheduler_limits": limits.model_dump(),
                    },
                    idempotency_key=f"hypothesis:{item['id']}:{uuid4()}",
                )
                state.active_job_id, state.last_attempt_at = job.id, now
            await _charge(session, job, daily, limits)
            for key, value in cost.items():
                used[key] += value
            places.add(item["location_id"])
            selected.append(
                {
                    "job_id": str(job.id),
                    "kind": item["kind"],
                    "target_id": str(item["id"]),
                    "tier": item["tier"],
                    "priority": item["priority"],
                }
            )
        session.add(
            SchedulerCycle(
                slot=slot,
                summary={
                    "enabled": True,
                    "selected": selected,
                    "resources": used,
                    "coverage_complete": not truncated,
                },
            )
        )
    pending = False
    if selected:
        try:
            async with ctx["sessions"]() as session:
                await dispatch_pending(
                    session, ctx["redis"], job_ids=[UUID(row["job_id"]) for row in selected]
                )
        except Exception:
            pending = True
    return {
        "enabled": True,
        "selected": selected,
        "resources": used,
        "coverage_complete": not truncated,
        "queue_pending": pending,
    }
