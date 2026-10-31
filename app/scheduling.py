"""Configurable deterministic research priorities and conservative reservations."""

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field


class ResourceBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    jobs: int = Field(default=2, ge=0, le=10000)
    queries: int = Field(default=36, ge=0, le=1000000)
    fetches: int = Field(default=120, ge=0, le=1000000)
    llm_calls: int = Field(default=144, ge=0, le=1000000)


class PriorityWeights(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    demand: int = Field(default=2, ge=0, le=1000)
    staleness: int = Field(default=5, ge=0, le=1000)
    uncertainty: int = Field(default=15, ge=0, le=1000)
    activity: int = Field(default=5, ge=0, le=1000)
    incompleteness: int = Field(default=30, ge=0, le=1000)
    first_deep: int = Field(default=60, ge=0, le=1000)
    new_hypothesis: int = Field(default=40, ge=0, le=1000)


class SchedulerPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    candidate_limit: int = Field(default=200, ge=1, le=1000)
    hot_requests_24h: int = Field(default=10, ge=1)
    active_requests_7d: int = Field(default=3, ge=1)
    burst_sources_7d: int = Field(default=3, ge=1)
    hot_seconds: int = Field(default=8 * 3600, ge=60, le=31536000)
    active_seconds: int = Field(default=2 * 86400, ge=60, le=31536000)
    normal_seconds: int = Field(default=10 * 86400, ge=60, le=31536000)
    dormant_days: int = Field(default=90, ge=31, le=365)
    cycle: ResourceBudget = Field(default_factory=ResourceBudget)
    daily: ResourceBudget = Field(
        default_factory=lambda: ResourceBudget(jobs=20, queries=360, fetches=1200, llm_calls=1440)
    )
    weights: PriorityWeights = Field(default_factory=PriorityWeights)


def tier(candidate, policy, now):
    if (
        candidate["d24"] >= policy.hot_requests_24h
        or candidate["activity"] >= policy.burst_sources_7d
    ):
        return "HOT"
    if candidate["d7"] >= policy.active_requests_7d or candidate["activity"]:
        return "ACTIVE"
    if candidate["d30"]:
        return "NORMAL"
    last = candidate.get("last_demand") or candidate.get("last_run")
    return "COLD" if last and last > now - timedelta(days=policy.dormant_days) else "DORMANT"


def priority(candidate, policy, now):
    level = tier(candidate, policy, now)
    if level in {"COLD", "DORMANT"} or candidate["busy"]:
        return None
    if candidate.get("next_retry") and candidate["next_retry"] > now:
        return None
    interval = getattr(policy, level.lower() + "_seconds")
    last = candidate.get("last_run")
    if last and now - last < timedelta(seconds=interval):
        return None
    w = policy.weights
    demand = min(50, candidate["d24"] * 3 + candidate["d7"] + candidate["d30"] / 4)
    stale = min(10, max(0, (now - last).total_seconds() / interval)) if last else 10
    score = (
        w.demand * demand
        + w.staleness * stale
        + w.uncertainty * candidate["uncertain"]
        + w.activity * min(10, int(candidate["activity"]))
        + w.incompleteness * candidate["incomplete"]
        + w.first_deep * candidate["first_deep"]
        + w.new_hypothesis * candidate["new_hypothesis"]
    )
    return {**candidate, "tier": level, "priority": round(score, 6)}


def reservation(limits):
    return {
        "jobs": 1,
        "queries": limits.queries * 3,
        "fetches": limits.fetches * 3,
        "llm_calls": limits.llm_calls * 3,
    }


def fits(used, cost, budget):
    return all(used[key] + cost[key] <= value for key, value in budget.model_dump().items())
