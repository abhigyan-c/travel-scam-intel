"""Conservative dataset signals; no objective novelty or population-risk inference."""

import math
from datetime import timedelta

from app.frequency import utc

VERSION = "emergence-v1"
POLICY = {
    "window_days": 30,
    "growth_minimum_credit": 2.0,
    "growth_minimum_change": 1.0,
    "growth_ratio": 2.0,
    "novel_minimum_credit": 0.5,
    "similarity_threshold": 0.85,
}


def assess_pattern(
    reports,
    *,
    as_of,
    confidence,
    comparisons,
    similarity_complete,
    limitations,
    first_observed_at=None,
):
    """Reports are consolidated eligible sources/submissions, using current public credit."""
    as_of = utc(as_of)
    for row in reports:
        utc(row["first_seen"])
        if (
            type(row["credit"]) not in (int, float)
            or not math.isfinite(row["credit"])
            or not 0 <= row["credit"] <= 1
        ):
            raise ValueError("invalid_report_credit")
    valid = [row for row in reports if row["first_seen"] <= as_of]
    limits = set(limitations)
    if len(valid) != len(reports):
        limits.add("future_dataset_observation")
    first = min((row["first_seen"] for row in valid), default=None)
    if first_observed_at is not None and utc(first_observed_at) <= as_of:
        first = min(first, first_observed_at) if first else first_observed_at
    if first is None:
        limits.add("dataset_observation_missing")
    window = timedelta(days=POLICY["window_days"])
    recent = [row for row in valid if as_of - window < row["first_seen"] <= as_of]
    previous = [row for row in valid if as_of - 2 * window < row["first_seen"] <= as_of - window]
    current_credit = round(sum(row["credit"] for row in recent), 6)
    prior_credit = round(sum(row["credit"] for row in previous), 6)
    growth = "insufficient_data"
    if current_credit + prior_credit >= POLICY["growth_minimum_credit"]:
        if (
            current_credit >= POLICY["growth_ratio"] * prior_credit
            and current_credit - prior_credit >= POLICY["growth_minimum_change"]
        ):
            growth = "rising"
        elif (
            prior_credit >= POLICY["growth_ratio"] * current_credit
            and prior_credit - current_credit >= POLICY["growth_minimum_change"]
        ):
            growth = "falling"
        else:
            growth = "stable"
    novelty = (
        "unknown"
        if first is None
        else "first_observed_recently"
        if first > as_of - window
        else "previously_observed"
    )
    older_similar = [
        row
        for row in comparisons
        if row["similarity"] >= POLICY["similarity_threshold"]
        and first is not None
        and row["first_observed_at"] < first
    ]
    geography = any(row["scope"] == "other" and not row["provisional"] for row in older_similar)
    reasons = []
    if novelty == "first_observed_recently" and current_credit >= POLICY["novel_minimum_credit"]:
        reasons.append("first_observed_in_our_dataset")
        if geography:
            reasons.append("possible_new_geographic_appearance_in_dataset")
    if growth == "rising":
        reasons.append("independent_reporting_growth")
    if not similarity_complete:
        limits.add("similarity_coverage_incomplete")
    return {
        "candidate": bool(reasons),
        "reasons": reasons,
        "novelty": {
            "status": novelty,
            "first_observed_at": first.isoformat() if first else None,
            "older_similar_hints": len(older_similar),
            "interpretation": "first observed in our eligible dataset; not world novelty",
        },
        "growth": {
            "status": growth,
            "current_reports": len(recent),
            "previous_reports": len(previous),
            "current_credit": current_credit,
            "previous_credit": prior_credit,
            "ratio": round(current_credit / prior_credit, 6) if prior_credit else None,
            "basis": "first_dataset_observation_per_source_or_submission",
            "comparison": "current_30_days_vs_previous_30_days_with_current_eligibility_and_credit",
        },
        "confidence": confidence,
        "geography": {
            "possible_new_appearance": "possible_new_geographic_appearance_in_dataset" in reasons,
            "interpretation": "possible dataset appearance; not geographic spread",
        },
        "comparisons": [
            {**row, "first_observed_at": row["first_observed_at"].isoformat()}
            for row in comparisons
        ],
        "complete": not limits,
        "limitations": sorted(limits),
        "label": "emerging candidate"
        if growth == "rising"
        else "first observed in our dataset"
        if reasons
        else "no current emerging signal",
        "policy": POLICY,
        "publication": "private review only; no public label or confidence change",
    }
