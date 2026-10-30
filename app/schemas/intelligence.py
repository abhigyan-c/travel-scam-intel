from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "intelligence-v1"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LocationRef(Contract):
    id: UUID
    name: str
    type: str
    country_code: str | None
    provisional: bool


class ResearchState(Contract):
    status: Literal["never_researched", "researching", "ready", "stale", "failed"]
    initial: bool = False
    enriched: bool = False
    in_progress: bool = False
    stale: bool = False
    complete: bool = False
    last_success_at: datetime | None = None
    fresh_until: datetime | None = None


class ReportCount(Contract):
    raw_report_count: int
    independence_weighted_report_count: float


class PublicMetrics(Contract):
    status: Literal["current", "unavailable"]
    as_of: datetime
    confidence_score: float | None = None
    confidence_label: Literal["no_support", "limited", "moderate", "strong", "unknown"] = "unknown"
    support_weight: float | None = None
    contradiction_weight: float | None = None
    recency_score: float | None = None
    prevalence_score: float | None = None
    all_time: ReportCount | None = None
    occurrence_windows: dict[str, ReportCount] = Field(default_factory=dict)
    reporting_windows: dict[str, ReportCount] = Field(default_factory=dict)
    dated_reports: int | None = None
    undated_or_invalid_reports: int | None = None
    trend: str = "unknown"
    complete: bool = False
    limitations: list[str] = Field(default_factory=list)
    policy_version: str = "public-intelligence-v1"
    interpretation: str = (
        "corroboration and observed reporting; not truth probability or population incidence"
    )


class HypothesisCard(Contract):
    id: UUID
    title: str
    description: str
    category: str
    status: Literal["candidate", "active", "disputed"]
    location: LocationRef
    location_path: list[LocationRef]
    scope: Literal["exact", "ancestor", "descendant", "other"]
    research: ResearchState
    metrics: PublicMetrics


class HypothesisPage(Contract):
    contract_version: str = CONTRACT_VERSION
    requested_location: LocationRef
    research: ResearchState
    items: list[HypothesisCard]
    total: int
    limit: int
    offset: int
    has_more: bool
    outcome: Literal["findings", "nothing_found", "research_incomplete"]


class PublicEvidence(Contract):
    id: UUID
    stance: str
    source_type: str
    provenance_class: str
    label: Literal["public_web_source", "anonymous_traveler_report"]
    platform: str | None = None
    source_url: str | None = None
    claim: str | None = None
    excerpt: str | None = None
    published_at: datetime | None = None
    occurred_at: datetime | None = None
    source_quality: float | None = None
    independence_credit: float | None = None
    evidence_weight: float | None = None
    discounted: bool | None = None


class EvidencePage(Contract):
    contract_version: str = CONTRACT_VERSION
    hypothesis_id: UUID
    location: LocationRef
    research: ResearchState
    items: list[PublicEvidence]
    total: int
    limit: int
    offset: int
    has_more: bool
    metrics_status: Literal["current", "unavailable"]


class RelatedCard(Contract):
    relation: str
    direction: Literal["outgoing", "incoming", "symmetric"]
    hypothesis: HypothesisCard


class RelatedPage(Contract):
    contract_version: str = CONTRACT_VERSION
    hypothesis_id: UUID
    items: list[RelatedCard]
    total: int
    limit: int
    offset: int
    has_more: bool


class LocationSummary(Contract):
    contract_version: str = CONTRACT_VERSION
    location: LocationRef
    research: ResearchState
    exact_patterns: int
    inherited_patterns: int
    descendant_patterns: int
    total_patterns: int
    categories: dict[str, int]
    categories_complete: bool
    outcome: Literal["findings", "nothing_found", "research_incomplete"]


class IncidentMatch(Contract):
    similarity: float
    hypothesis: HypothesisCard


class IncidentMatches(Contract):
    contract_version: str = CONTRACT_VERSION
    requested_location: LocationRef
    research: ResearchState
    status: Literal["matched", "no_match", "unavailable"]
    complete: bool
    method: str = "local_embedding_of_public_support"
    items: list[IncidentMatch]
    limitations: list[str] = Field(default_factory=list)
    interpretation: str = "semantic similarity, not incident verification or evidence attachment"
