from datetime import datetime
from typing import Literal
from uuid import UUID

from app.schemas.intelligence import Contract, LocationRef, PublicMetrics

Freshness = Literal["fresh", "stale", "unknown"]
StopReason = Literal[
    "time_limit",
    "resource_limit",
    "source_failures",
    "provider_unavailable",
    "processing_incomplete",
    "review_pending",
    "retry_exhausted",
    "superseded",
]


class RunProgress(Contract):
    status: Literal["queued", "researching", "completed", "partial", "failed"]
    terminal: bool
    created_at: datetime | None
    started_at: datetime | None
    last_checked_at: datetime | None
    stop_reasons: list[StopReason]


class RetainedReport(Contract):
    coverage: Literal["complete", "partial", "unknown"]
    freshness: Freshness
    fresh_until: datetime | None
    last_result_update_at: datetime | None
    last_completed_at: datetime | None


class ReportPattern(Contract):
    id: UUID
    title: str
    description: str
    category: str
    status: Literal["candidate", "active", "disputed"]
    location: LocationRef
    location_path: list[LocationRef]
    scope: Literal["exact", "ancestor", "descendant", "other"]
    metrics: PublicMetrics
    source_checked_at: datetime | None
    freshness: Freshness


class WatchState(Contract):
    id: UUID
    expires_at: datetime
    waits_remaining: int


class ResearchReport(Contract):
    contract_version: Literal["research-report-v1"]
    run_id: UUID
    location: LocationRef
    revision: str
    latest_run: RunProgress
    retained_report: RetainedReport
    superseded: bool
    items: list[ReportPattern]
    total: int
    limit: int
    offset: int
    has_more: bool
    membership_limit: int
    membership_may_be_truncated: bool
    outcome: Literal["findings", "nothing_found", "research_incomplete"]
    next_check_after_seconds: int
    watch: WatchState | None
    queue_pending: bool = False
    wait_status: (
        Literal["capacity", "expired", "limit_reached", "terminal", "changed", "unchanged"] | None
    ) = None
