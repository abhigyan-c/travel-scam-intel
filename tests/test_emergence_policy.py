from datetime import UTC, datetime, timedelta

import pytest

from app.emergence import assess_pattern

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def assess(ages, *, credits=None, comparisons=None, complete=True):
    return assess_pattern(
        [
            {"first_seen": NOW - timedelta(days=age), "credit": credit}
            for age, credit in zip(ages, credits or [1.0] * len(ages), strict=True)
        ],
        as_of=NOW,
        confidence={"score": 0.12, "label": "limited"},
        comparisons=comparisons or [],
        similarity_complete=complete,
        limitations=[],
    )


def test_new_dataset_observation_is_separate_from_confidence_and_growth():
    result = assess([1])
    assert result["candidate"] and result["reasons"] == ["first_observed_in_our_dataset"]
    assert result["growth"]["status"] == "insufficient_data"
    assert result["confidence"] == {"score": 0.12, "label": "limited"}
    assert "world novelty" in result["novelty"]["interpretation"]


def test_growth_with_zero_baseline_is_not_infinite_or_proof_of_novelty():
    result = assess([100, 1, 2, 3])
    assert result["novelty"]["status"] == "previously_observed"
    assert result["growth"]["status"] == "rising" and result["growth"]["ratio"] is None
    assert result["reasons"] == ["independent_reporting_growth"]


@pytest.mark.parametrize(
    "ages,status",
    [
        ([1, 2, 40], "rising"),
        ([1, 40, 45], "falling"),
        ([1, 40], "stable"),
        ([100], "insufficient_data"),
    ],
)
def test_historical_growth_windows(ages, status):
    assert assess(ages)["growth"]["status"] == status


def test_copied_low_credit_counts_do_not_create_growth():
    result = assess([1] * 20, credits=[0.01] * 20)
    assert not result["candidate"] and result["growth"]["status"] == "insufficient_data"
    assert result["growth"]["current_reports"] == 20


def test_window_boundaries_and_future_observations_are_explicit():
    result = assess([0, 30, 60, -1])
    assert result["growth"]["current_reports"] == 1 and result["growth"]["previous_reports"] == 1
    assert result["novelty"]["first_observed_at"] == (NOW - timedelta(days=60)).isoformat()
    assert not result["complete"] and "future_dataset_observation" in result["limitations"]


@pytest.mark.parametrize(
    "scope,provisional,expected",
    [
        ("other", False, True),
        ("ancestor", False, False),
        ("descendant", False, False),
        ("other", True, False),
    ],
)
def test_geography_is_only_a_distinct_established_location_hint(scope, provisional, expected):
    comparison = {
        "similarity": 0.95,
        "scope": scope,
        "provisional": provisional,
        "first_observed_at": NOW - timedelta(days=80),
    }
    result = assess([1], comparisons=[comparison], complete=False)
    assert result["geography"]["possible_new_appearance"] is expected
    assert "similarity_coverage_incomplete" in result["limitations"]


@pytest.mark.parametrize("credit", [-1, 2, float("nan"), True])
def test_invalid_credit_is_never_used(credit):
    with pytest.raises(ValueError):
        assess([1], credits=[credit])
