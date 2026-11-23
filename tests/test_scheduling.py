from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.db.jobs import validate_payload
from app.discovery import generate_queries
from app.models.research_job import JobKind
from app.research import ResearchLimits
from app.scheduling import ResourceBudget, SchedulerPolicy, fits, priority, reservation, tier

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def candidate(**changes):
    return {
        "d24": 0,
        "d7": 0,
        "d30": 1,
        "last_demand": NOW,
        "activity": 0,
        "last_run": NOW - timedelta(days=20),
        "next_retry": None,
        "busy": False,
        "uncertain": False,
        "incomplete": False,
        "first_deep": False,
        "new_hypothesis": False,
        **changes,
    }


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"d24": 10}, "HOT"),
        ({"d7": 3}, "ACTIVE"),
        ({}, "NORMAL"),
        ({"d30": 0, "last_demand": NOW - timedelta(days=40)}, "COLD"),
        ({"d30": 0, "last_demand": NOW - timedelta(days=100), "last_run": None}, "DORMANT"),
        ({"activity": 3}, "HOT"),
    ],
)
def test_tiers_decay_with_demand_and_activity(changes, expected):
    assert tier(candidate(**changes), SchedulerPolicy(), NOW) == expected


def test_tier_intervals_and_cold_dormant_never_schedule_proactively():
    p = SchedulerPolicy()
    assert priority(candidate(d24=10, last_run=NOW - timedelta(hours=7)), p, NOW) is None
    assert priority(candidate(d24=10, last_run=NOW - timedelta(hours=9)), p, NOW)
    assert priority(candidate(d7=3, last_run=NOW - timedelta(days=1)), p, NOW) is None
    assert priority(candidate(last_run=NOW - timedelta(days=9)), p, NOW) is None
    assert priority(candidate(d30=0, last_demand=None, last_run=None), p, NOW) is None
    tuned = p.model_copy(update={"normal_seconds": 3600})
    assert priority(candidate(last_run=NOW - timedelta(hours=2)), tuned, NOW)


def test_first_deep_new_hypothesis_and_uncertainty_outrank_routine_refresh():
    p = SchedulerPolicy()
    routine = priority(candidate(), p, NOW)["priority"]
    for changes in (
        {"first_deep": True},
        {"new_hypothesis": True},
        {"uncertain": True},
        {"incomplete": True},
    ):
        assert priority(candidate(**changes), p, NOW)["priority"] > routine
    assert priority(candidate(busy=True), p, NOW) is None
    assert priority(candidate(next_retry=NOW + timedelta(minutes=1)), p, NOW) is None


def test_resource_limits_reserve_every_attempt_and_every_budget_dimension():
    cost = reservation(ResearchLimits(queries=2, fetches=3, llm_calls=4))
    assert cost == {"jobs": 1, "queries": 6, "fetches": 9, "llm_calls": 12}
    used = dict.fromkeys(cost, 0)
    assert fits(used, cost, ResourceBudget(jobs=1, queries=6, fetches=9, llm_calls=12))
    for field in cost:
        budget = ResourceBudget(jobs=1, queries=6, fetches=9, llm_calls=12).model_copy(
            update={field: cost[field] - 1}
        )
        assert not fits(used, cost, budget)


def test_scheduled_payload_persists_limits_and_rejects_unbudgeted_continuation():
    payload = {
        "location_id": "00000000-0000-0000-0000-000000000001",
        "phase": "refresh",
        "scheduler_limits": ResearchLimits().model_dump(),
    }
    assert validate_payload(JobKind.LOCATION_RESEARCH, payload) == payload
    with pytest.raises(ValueError):
        validate_payload(JobKind.LOCATION_RESEARCH, {**payload, "phase": "initial"})
    assert "scheduler_limits" in validate_payload(
        JobKind.HYPOTHESIS_RESEARCH,
        {"hypothesis_id": payload["location_id"], "scheduler_limits": payload["scheduler_limits"]},
    )


def test_hypothesis_queries_preserve_resolved_geography():
    queries = generate_queries("Nadi, Fiji", topic="extra money after an agreed fare")
    assert len(queries) == 6 and all(q.startswith("Nadi, Fiji ") for q in queries)
    assert all("agreed fare" in q for q in queries)


def test_scheduler_json_configuration_is_opt_in_and_preserves_unspecified_defaults(monkeypatch):
    monkeypatch.delenv("APP_SCHEDULER", raising=False)
    assert not Settings(_env_file=None).scheduler.enabled
    monkeypatch.setenv(
        "APP_SCHEDULER", '{"enabled":true,"cycle":{"jobs":1},"weights":{"uncertainty":99}}'
    )
    policy = Settings(_env_file=None).scheduler
    assert policy.enabled and policy.cycle.jobs == 1 and policy.cycle.fetches == 120
    assert policy.weights.uncertainty == 99 and policy.hot_seconds == 8 * 3600
