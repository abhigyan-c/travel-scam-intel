from argparse import ArgumentTypeError
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.calculate_frequency import reference_date
from app.frequency import calculate_frequency, utc

AS_OF = datetime(2026, 10, 28, 12, tzinfo=UTC)


def item(index=0, *, days=0, **changes):
    return {
        "evidence_id": str(index),
        "claim_id": str(index),
        "document_id": str(index),
        "report_key": f"source:{index}",
        "eligible": True,
        "stance": "supports",
        "location_agreement": "exact",
        "independence": 1.0,
        "occurred_at": (AS_OF - timedelta(days=days)).isoformat(),
        "occurrence_basis": "claim",
        "reported_at": None,
        "reporting_basis": "unknown",
        **changes,
    }


def measure(items):
    return calculate_frequency(items, as_of=AS_OF)


def test_windows_boundaries_and_all_time_include_undated_reports():
    result = measure(
        [item(i, days=days) for i, days in enumerate([0, 29, 30, 89, 90, 364, 365])]
        + [item(8, occurred_at=None)]
    )
    assert result["all_time"]["raw_report_count"] == 8
    assert {key: row["raw_report_count"] for key, row in result["occurrence_windows"].items()} == {
        "30": 2,
        "90": 4,
        "365": 6,
    }
    assert result["date_coverage"]["occurrence_undated_or_invalid_reports"] == 1
    assert result["date_review_reasons"] == ["occurrence_date_missing"]


def test_occurrence_recency_does_not_fall_back_to_recent_publication_or_ingestion():
    result = measure([item(days=365, reported_at=AS_OF.isoformat(), reporting_basis="document")])
    assert result["recency_score"] == round(2 ** (-365 / 90), 6)
    assert result["prevalence_score"] == 0
    assert result["occurrence_windows"]["30"]["raw_report_count"] == 0
    assert result["reporting_windows"]["30"]["raw_report_count"] == 1
    missing = measure([item(occurred_at=None, reported_at=AS_OF.isoformat())])
    assert missing["recency_score"] is missing["prevalence_score"] is None
    assert missing["trend"]["classification"] == "unknown"


def test_decay_half_life_and_prevalence_are_independent_of_confidence():
    assert measure([item(days=90)])["recency_score"] == 0.5
    assert measure([item()])["recency_score"] == 1
    result = measure([item(), item(1, days=90)])
    assert result["recency_score"] == 0.75
    assert result["prevalence_score"] == round(1 / 3, 6)


def test_claims_versions_and_copies_do_not_multiply_weighted_report_count():
    reports = [item(), item(1, report_key="source:0", independence=0.25), item(2, independence=0)]
    result = measure(reports)
    assert result["all_time"] == {"raw_report_count": 2, "independence_weighted_report_count": 1}
    assert result["occurrence_windows"]["30"] == result["all_time"]
    assert len(result["reports"][0]["evidence_ids"]) == 2


def test_conflicting_dates_are_unknown_and_publication_uses_earliest_version():
    result = measure(
        [
            item(reported_at=(AS_OF - timedelta(days=100)).isoformat()),
            item(1, report_key="source:0", days=50, reported_at=AS_OF.isoformat()),
        ]
    )
    assert result["all_time"]["raw_report_count"] == 1
    assert result["recency_score"] is None
    assert result["date_review_reasons"] == ["conflicting_occurrence_dates"]
    assert result["reporting_windows"]["90"]["raw_report_count"] == 0


def test_future_dates_do_not_become_fresh_observations():
    result = measure([item(days=-1, reported_at=(AS_OF + timedelta(days=1)).isoformat())])
    assert result["all_time"]["raw_report_count"] == 1
    assert result["occurrence_windows"]["30"]["raw_report_count"] == 0
    assert result["reporting_windows"]["30"]["raw_report_count"] == 0
    assert result["recency_score"] is None
    assert result["date_review_reasons"] == ["future_occurrence_date"]


@pytest.mark.parametrize(
    "change",
    [
        {"eligible": False},
        {"stance": "contradicts"},
        {"stance": "uncertain"},
        {"stance": "related"},
        {"location_agreement": "different"},
        {"location_agreement": "unknown"},
    ],
)
def test_ineligible_or_other_location_evidence_does_not_inflate_frequency(change):
    result = measure([item(**change)])
    assert result["all_time"]["raw_report_count"] == 0
    assert result["recency_score"] is result["prevalence_score"] is None
    assert result["excluded_evidence"][0]["reasons"]


@pytest.mark.parametrize(
    "recent,prior,expected",
    [
        (3, 0, "rising"),
        (0, 3, "falling"),
        (2, 2, "stable"),
        (1, 0, "insufficient_data"),
        (0, 0, "no_recent_reports"),
    ],
)
def test_trend_uses_equal_nonoverlapping_periods_and_minimum_credit(recent, prior, expected):
    reports = [item(i, days=5) for i in range(recent)]
    reports += [item(10 + i, days=30) for i in range(prior)]
    reports += [item(100, days=100)]
    result = measure(reports)
    assert result["trend"]["classification"] == expected
    assert result["trend"]["previous_30_days"]["raw_report_count"] == prior


def test_duplicates_cannot_create_a_rising_trend_or_refresh_old_evidence():
    reports = [item(days=100)] + [item(i, independence=0) for i in range(1, 10)]
    result = measure(reports)
    assert result["trend"]["classification"] == "no_recent_reports"
    assert result["recency_score"] == round(2 ** (-100 / 90), 6)


def test_timezone_equivalence_order_and_empty_result_are_deterministic():
    one = item(occurred_at=AS_OF.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat())
    two = item(1, days=90)
    assert measure([one, two]) == measure([two, one])
    assert measure([one])["recency_score"] == 1
    empty = measure([])
    assert empty["all_time"]["raw_report_count"] == 0
    assert empty["recency_score"] is empty["prevalence_score"] is None


@pytest.mark.parametrize("credit", [-1, 1.01, float("nan"), float("inf"), True, "0.5"])
def test_invalid_credit_is_rejected(credit):
    with pytest.raises(ValueError, match="finite_unit_interval"):
        measure([item(independence=credit)])


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError, match="timezone_aware"):
        utc(datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="timezone_aware"):
        measure([item(occurred_at="2026-10-28T12:00:00")])


@pytest.mark.parametrize("value", ["2026-10-28", "2026-10-28T12:00:00", "invalid"])
def test_cli_reference_requires_explicit_timezone(value):
    with pytest.raises(ArgumentTypeError, match="timezone"):
        reference_date(value)


def test_cli_reference_normalizes_offset_to_utc():
    assert reference_date("2026-10-28T17:30:00+05:30") == AS_OF
