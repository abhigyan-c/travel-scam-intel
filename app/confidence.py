"""Versioned corroboration heuristic; scores are not truth probabilities."""

import math

VERSION = "confidence-v1"
POLICY = {
    "support_saturation": 2.0,
    "contradiction_penalty": 2.0,
    "unknown_semantic_factor": 0.5,
    "unknown_location_factor": 0.5,
    "diversity_baseline": 0.8,
    "type_diversity_bonus": 0.1,
    "platform_diversity_bonus": 0.1,
    "diversity_extra_categories_cap": 2,
    "limited_below": 0.25,
    "strong_at": 0.6,
}


def _unit(value):
    if type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("confidence_input_must_be_finite_unit_interval")
    return float(value)


def score_confidence(items: list[dict], *, complete: bool, review_reasons: list[str]) -> dict:
    support = contradiction = 0.0
    types, platforms = set(), set()
    contributions = []
    unknowns = set()
    for item in sorted(items, key=lambda value: value["evidence_id"]):
        weight = _unit(item["weight"])
        semantic = item["semantic_match_score"]
        if semantic is None:
            semantic = POLICY["unknown_semantic_factor"]
            if weight:
                unknowns.add("semantic_agreement_unknown")
        semantic = _unit(semantic)
        location = item["location_agreement"]
        if location not in ("exact", "unknown", "different"):
            raise ValueError("invalid_location_agreement")
        factor = {"exact": 1.0, "unknown": POLICY["unknown_location_factor"], "different": 0.0}[
            location
        ]
        if weight and location == "unknown":
            unknowns.add("location_agreement_unknown")
        effective = (
            weight * semantic * factor if item["stance"] in ("supports", "contradicts") else 0.0
        )
        if item["stance"] == "supports":
            support += effective
            if effective:
                types.add(item["source_type"])
                if item["platform"]:
                    platforms.add(item["platform"])
        elif item["stance"] == "contradicts":
            contradiction += effective
        contributions.append(
            {
                **item,
                "semantic_factor": semantic,
                "location_factor": factor,
                "effective_weight": round(effective, 9),
            }
        )
    cap = POLICY["diversity_extra_categories_cap"]
    diversity = POLICY["diversity_baseline"] + sum(
        POLICY[bonus] * min(cap, max(0, len(values) - 1)) / cap
        for values, bonus in (
            (types, "type_diversity_bonus"),
            (platforms, "platform_diversity_bonus"),
        )
    )
    score = (
        support
        / (POLICY["support_saturation"] + support + POLICY["contradiction_penalty"] * contradiction)
        * diversity
    )
    score = round(score, 6)
    label = (
        "no_support"
        if support == 0
        else "limited"
        if score < POLICY["limited_below"]
        else "moderate"
        if score < POLICY["strong_at"]
        else "strong"
    )
    reasons = sorted(set(review_reasons) | unknowns)
    components = {
        "version": VERSION,
        "policy": POLICY,
        "formula": "diversity * support / (2 + support + 2 * contradiction)",
        "support_weight": round(support, 9),
        "contradiction_weight": round(contradiction, 9),
        "supporting_source_types": sorted(types),
        "supporting_platforms": sorted(platforms),
        "diversity_factor": round(diversity, 6),
        "label": label,
        "complete": complete,
        "review_reasons": reasons,
        "contributions": contributions,
        "interpretation": "corroboration heuristic, not a truth probability or incidence estimate",
    }
    explanation = (
        f"{label.replace('_', ' ').capitalize()} corroboration: supporting weight {support:.3f}, "
        f"contradicting weight {contradiction:.3f}, diversity factor {diversity:.2f}. "
        "Weights include eligibility, independence, semantic and location agreement. "
        + (
            "Upstream provenance assessment is incomplete; review required. "
            if not complete
            else ""
        )
        + "This score is not a probability that an allegation is true."
    )
    return {"score": score, "components": components, "explanation": explanation}
