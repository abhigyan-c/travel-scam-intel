"""Observed reporting frequency and occurrence recency, never population incidence."""

import math
from datetime import UTC, datetime

VERSION = "frequency-v1"
POLICY = {
    "windows_days": [30, 90, 365],
    "recency_half_life_days": 90,
    "prevalence_window_days": 90,
    "prevalence_saturation": 2.0,
    "trend_period_days": 30,
    "trend_minimum_credit": 2.0,
    "trend_ratio": 1.5,
    "trend_minimum_change": 1.0,
}


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone_aware_datetime_required")
    return value.astimezone(UTC)


def _date(value):
    return utc(datetime.fromisoformat(value)) if value is not None else None


def _count(reports):
    return {
        "raw_report_count": len(reports),
        "independence_weighted_report_count": round(sum(row["independence"] for row in reports), 9),
    }


def _windows(reports, field, as_of):
    return {
        str(days): _count(
            [
                row
                for row in reports
                if row[field] is not None
                and 0 <= (as_of - _date(row[field])).total_seconds() / 86400 < days
            ]
        )
        for days in POLICY["windows_days"]
    }


def calculate_frequency(items: list[dict], *, as_of: datetime) -> dict:
    """One report per normalized submission or external source, not per claim/version."""
    as_of = utc(as_of)
    groups, excluded = {}, []
    for item in sorted(items, key=lambda row: row["evidence_id"]):
        credit = item["independence"]
        if type(credit) not in (float, int) or not math.isfinite(credit) or not 0 <= credit <= 1:
            raise ValueError("independence_must_be_finite_unit_interval")
        reasons = []
        if not item["eligible"]:
            reasons.append("publication_not_eligible")
        if item["stance"] != "supports":
            reasons.append("not_supporting")
        if item["location_agreement"] != "exact":
            reasons.append("location_not_exact")
        if reasons:
            excluded.append({"evidence_id": item["evidence_id"], "reasons": reasons})
        else:
            groups.setdefault(item["report_key"], []).append(item)
    reports, date_issues = [], set()
    for key, members in sorted(groups.items()):
        dates = sorted(
            {
                _date(row["occurred_at"]).isoformat()
                for row in members
                if row["occurred_at"] is not None
            }
        )
        occurred = dates[0] if len(dates) == 1 else None
        issue = (
            "conflicting_occurrence_dates"
            if len(dates) > 1
            else "occurrence_date_missing"
            if not dates
            else None
        )
        if occurred is not None and _date(occurred) > as_of:
            issue = "future_occurrence_date"
            occurred = None
        if issue:
            date_issues.add(issue)
        # Earliest publication/submission across versions cannot be refreshed by an edit.
        reporting = sorted(
            {
                _date(row["reported_at"]).isoformat()
                for row in members
                if row["reported_at"] is not None
            }
        )
        reported = reporting[0] if reporting else None
        reporting_issue = None
        if reported is not None and _date(reported) > as_of:
            reporting_issue, reported = "future_reporting_date", None
        elif reported is None:
            reporting_issue = "reporting_date_missing"
        reports.append(
            {
                "report_key": key,
                "evidence_ids": sorted(row["evidence_id"] for row in members),
                "document_ids": sorted({row["document_id"] for row in members}),
                "independence": max(row["independence"] for row in members),
                "occurred_at": occurred,
                "occurrence_candidates": dates,
                "occurrence_date_issue": issue,
                "occurrence_bases": sorted({row["occurrence_basis"] for row in members}),
                "reported_at": reported,
                "reporting_date_issue": reporting_issue,
                "reporting_bases": sorted({row["reporting_basis"] for row in members}),
            }
        )
    windows = _windows(reports, "occurred_at", as_of)
    reporting_windows = _windows(reports, "reported_at", as_of)
    dated = [row for row in reports if row["occurred_at"] is not None]
    dated_credit = sum(row["independence"] for row in dated)
    decayed = sum(
        row["independence"]
        * 2
        ** (
            -(as_of - _date(row["occurred_at"])).total_seconds()
            / 86400
            / POLICY["recency_half_life_days"]
        )
        for row in dated
    )
    period = POLICY["trend_period_days"]
    recent = windows[str(period)]["independence_weighted_report_count"]
    previous = _count(
        [
            row
            for row in dated
            if period <= (as_of - _date(row["occurred_at"])).total_seconds() / 86400 < 2 * period
        ]
    )
    prior = previous["independence_weighted_report_count"]
    if not dated_credit:
        trend = "unknown"
    elif recent + prior == 0:
        trend = "no_recent_reports"
    elif recent + prior < POLICY["trend_minimum_credit"]:
        trend = "insufficient_data"
    elif (
        recent >= POLICY["trend_ratio"] * prior and recent - prior >= POLICY["trend_minimum_change"]
    ):
        trend = "rising"
    elif (
        prior >= POLICY["trend_ratio"] * recent and prior - recent >= POLICY["trend_minimum_change"]
    ):
        trend = "falling"
    else:
        trend = "stable"
    recent_weight = windows[str(POLICY["prevalence_window_days"])][
        "independence_weighted_report_count"
    ]
    return {
        "version": VERSION,
        "policy": POLICY,
        "as_of": as_of.isoformat(),
        "all_time": _count(reports),
        "occurrence_windows": windows,
        "reporting_windows": reporting_windows,
        "date_coverage": {
            "occurrence_dated_reports": len(dated),
            "occurrence_undated_or_invalid_reports": len(reports) - len(dated),
            "reporting_dated_reports": sum(row["reported_at"] is not None for row in reports),
        },
        "decayed_independence_weight": round(decayed, 9),
        "recency_score": round(decayed / dated_credit, 6) if dated_credit else None,
        "prevalence_score": round(
            recent_weight / (POLICY["prevalence_saturation"] + recent_weight), 6
        )
        if dated_credit
        else None,
        "trend": {
            "classification": trend,
            "current_30_days": windows[str(period)],
            "previous_30_days": previous,
        },
        "reports": reports,
        "excluded_evidence": excluded,
        "date_review_reasons": sorted(date_issues),
        "interpretation": (
            "observed reporting frequency, not population incidence or exposure-adjusted risk"
        ),
    }
