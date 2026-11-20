"""Bounded report/watch services. Reads never queue work or invoke providers."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select, update

from app.db.research_runs import new_run, public_observations
from app.models import Hypothesis, LocationResearchState, ReportWatch, ResearchJob, ResearchRun
from app.models.research_job import JobStatus
from app.on_demand import request_location_research
from app.schemas.research_report import ResearchReport
from app.services.intelligence import (
    IntelligenceInputError,
    IntelligenceNotFound,
    IntelligenceService,
    current_hypothesis,
)


def freshness(observed, now, seconds):
    known = [datetime.fromisoformat(x) for x in observed if x]
    until = min(known) + timedelta(seconds=seconds) if known else None
    if until and until <= now:
        return "stale", until
    if not observed or len(known) != len(observed):
        return "unknown", None
    return "fresh", until


def stable(value):
    if isinstance(value, dict):
        return {
            k: stable(v)
            for k, v in value.items()
            if k not in {"as_of", "watch", "wait_status", "queue_pending"}
        }
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


class ResearchReports:
    def __init__(self, sessions, settings, *, clock=None):
        self.sessions, self.settings = sessions, settings
        self.clock = clock or (lambda: datetime.now(UTC))
        self.active_waits = 0

    async def get(self, run_id, *, limit=5, offset=0, watch_id=None):
        now = self.clock()
        async with IntelligenceService(self.sessions)._read(now) as query:
            run = await query.session.get(ResearchRun, run_id)
            if run is None:
                raise IntelligenceNotFound("report_run_not_found")
            ids = [UUID(i) for i in run.pattern_ids]
            page = await query.page(run.location_id, pattern_ids=ids, limit=limit, offset=offset)
            visible = list(
                await query.session.scalars(
                    select(Hypothesis.id).where(
                        current_hypothesis(),
                        Hypothesis.id.in_(ids),
                        await query.scope(run.location_id),
                    )
                )
            )
            checked = await public_observations(query.session, run, visible)
            observations = [checked.get(str(i)) for i in visible]
            if not visible and run.coverage == "complete" and run.last_completed_at:
                observations = [run.last_completed_at.isoformat()]
            age, until = freshness(observations, now, self.settings.research_fresh_seconds)
            current = await query.session.get(LocationResearchState, run.location_id)
            terminal = run.status not in {"queued", "researching"}
            status = run.status
            if not terminal:
                executing = await query.session.scalar(
                    select(ResearchJob.id)
                    .where(
                        ResearchJob.report_run_id == run.id,
                        ResearchJob.status == JobStatus.RUNNING,
                        ResearchJob.lease_expires_at > now,
                    )
                    .limit(1)
                )
                status = "researching" if executing else "queued"
            superseded = current is None or current.current_run_id != run.id
            abandoned = superseded and not terminal
            terminal = terminal or abandoned
            items = []
            for card in page.items:
                data = card.model_dump(mode="json", exclude={"research"})
                observation = checked.get(str(card.id))
                data.update(
                    source_checked_at=observation,
                    freshness=freshness([observation], now, self.settings.research_fresh_seconds)[
                        0
                    ],
                )
                items.append(data)
            result = dict(
                contract_version="research-report-v1",
                run_id=str(run.id),
                location=page.requested_location.model_dump(mode="json"),
                latest_run=dict(
                    status="failed" if abandoned else status,
                    terminal=terminal,
                    created_at=run.created_at.isoformat() if run.created_at else None,
                    started_at=run.started_at.isoformat() if run.started_at else None,
                    last_checked_at=run.finished_at.isoformat() if run.finished_at else None,
                    stop_reasons=["superseded"] if abandoned else run.stop_reasons,
                ),
                retained_report=dict(
                    coverage=run.coverage,
                    freshness=age,
                    fresh_until=until.isoformat() if until else None,
                    last_result_update_at=run.last_result_update_at.isoformat()
                    if run.last_result_update_at
                    else None,
                    last_completed_at=run.last_completed_at.isoformat()
                    if run.last_completed_at
                    else None,
                ),
                superseded=superseded,
                items=items,
                total=page.total,
                limit=limit,
                offset=offset,
                has_more=page.has_more,
                membership_limit=1000,
                membership_may_be_truncated=len(ids) >= 1000,
                outcome="findings"
                if page.total
                else "nothing_found"
                if run.status == "completed" and not ids
                else "research_incomplete",
                next_check_after_seconds=1,
            )
            result["revision"] = hashlib.sha256(
                json.dumps(stable(result), sort_keys=True).encode()
            ).hexdigest()
            watch = await query.session.get(ReportWatch, watch_id) if watch_id else None
            if watch_id and (watch is None or watch.run_id != run.id):
                raise IntelligenceNotFound("report_watch_not_found")
            result["watch"] = (
                dict(
                    id=str(watch.id),
                    expires_at=watch.expires_at.isoformat(),
                    waits_remaining=60 - watch.waits,
                )
                if watch
                else None
            )
            return ResearchReport.model_validate(result).model_dump(mode="json")

    async def request(self, ctx, location_id, *, follow_run_id=None, watch_seconds=300):
        watch_start = self.clock()
        if not 1 <= watch_seconds <= 600:
            raise IntelligenceInputError("invalid_watch_seconds")
        pending = False
        if follow_run_id is None:
            pending = await request_location_research(
                {**ctx, "settings": self.settings}, location_id
            )
            async with self.sessions() as session, session.begin():
                state = await session.scalar(
                    select(LocationResearchState)
                    .where(LocationResearchState.location_id == location_id)
                    .with_for_update()
                )
                active = (
                    await session.get(ResearchJob, state.active_job_id)
                    if state.active_job_id
                    else None
                )
                if state.current_run_id is None or (
                    state.status == "researching" and active and active.report_run_id is None
                ):
                    if state.status == "researching" and active:
                        # Do not wait for a worker's job lock while holding location
                        # state; a legacy-adoption race can safely retry this request.
                        await session.scalar(
                            select(ResearchJob)
                            .where(ResearchJob.id == active.id)
                            .with_for_update(nowait=True)
                        )
                    # Conservative adoption for legacy/operator-created state. Never
                    # invent historical successful-check or source-observation times.
                    run = await new_run(session, state)
                    run.created_at = None
                    run.status = (
                        "queued"
                        if state.status == "researching"
                        else ("failed" if state.status == "failed" else "partial")
                    )
                    if state.status == "researching" and active:
                        active.report_run_id = run.id
                run_id = state.current_run_id
        else:
            run_id = follow_run_id
            async with self.sessions() as session:
                run = await session.get(ResearchRun, run_id)
                if run is None or run.location_id != location_id:
                    raise IntelligenceNotFound("report_run_not_found")
        async with self.sessions() as session, session.begin():
            # Watches are operational control metadata, never provider/research allowances.
            await session.execute(
                delete(ReportWatch).where(ReportWatch.expires_at < self.clock() - timedelta(days=7))
            )
            watch = ReportWatch(
                run_id=run_id, expires_at=watch_start + timedelta(seconds=watch_seconds)
            )
            session.add(watch)
            await session.flush()
            watch_id = watch.id
        result = await self.get(run_id, watch_id=watch_id)
        return {**result, "queue_pending": pending}

    async def wait(self, run_id, watch_id, after_revision, *, wait_seconds=3, limit=5, offset=0):
        if not 0 <= wait_seconds <= 5:
            raise IntelligenceInputError("invalid_wait_seconds")
        if self.active_waits >= 8:
            return {
                **await self.get(run_id, watch_id=watch_id, limit=limit, offset=offset),
                "wait_status": "capacity",
            }
        self.active_waits += 1
        try:
            now = self.clock()
            async with self.sessions() as session, session.begin():
                watch = await session.get(ReportWatch, watch_id)
                if watch is None or watch.run_id != run_id:
                    raise IntelligenceNotFound("report_watch_not_found")
                expiry = watch.expires_at
                admitted = await session.scalar(
                    update(ReportWatch)
                    .where(
                        ReportWatch.id == watch_id,
                        ReportWatch.waits < 60,
                        ReportWatch.expires_at > now,
                    )
                    .values(waits=ReportWatch.waits + 1)
                    .returning(ReportWatch.id)
                )
            if admitted is None:
                return {
                    **await self.get(run_id, watch_id=watch_id, limit=limit, offset=offset),
                    "wait_status": "expired" if expiry <= now else "limit_reached",
                }
            deadline = asyncio.get_running_loop().time() + min(
                wait_seconds, max(0, (expiry - now).total_seconds())
            )
            while True:
                result = await self.get(run_id, watch_id=watch_id, limit=limit, offset=offset)
                if result["latest_run"]["terminal"]:
                    return {**result, "wait_status": "terminal"}
                if result["revision"] != after_revision:
                    return {**result, "wait_status": "changed"}
                if self.clock() >= expiry:
                    return {**result, "wait_status": "expired"}
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return {**result, "wait_status": "unchanged"}
                await asyncio.sleep(min(0.5, remaining))
        finally:
            self.active_waits -= 1
