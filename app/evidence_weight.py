"""Deterministic usefulness priors and publication gates, not allegation truth scores."""

VERSION = "evidence-weight-v1"
POLICY = {
    "source_priors": {
        "official_advisory": 0.75,
        "news": 0.65,
        "blog": 0.5,
        "forum": 0.5,
        "user_report": 0.5,
        "other": 0.35,
    },
    "provenance_adjustments": {
        "FIRST_HAND": 0.15,
        "WITNESSED": 0.1,
        "SECOND_HAND": -0.1,
        "UNKNOWN": -0.05,
    },
    "known_author_bonus": 0.05,
    "publication_metadata_bonus": 0.05,
    "verified_advisory_bonus": 0.1,
    "accepted_report_bonus": 0.05,
    "eligible_report_statuses": ["processed", "accepted"],
    "eligible_publication_statuses": ["unreviewed", "approved"],
    "eligible_stances": ["supports", "contradicts"],
}


def grade_evidence(item: dict) -> dict:
    source_type = "user_report" if item["report_id"] else item["source_type"]
    prior = POLICY["source_priors"][source_type]
    adjustments = [
        {
            "reason": "report_provenance",
            "value": POLICY["provenance_adjustments"][item["provenance"]],
        }
    ]
    unknown = []
    if item.get("invalid_provenance"):
        unknown.append("invalid_provenance_metadata")
    if item.get("invalid_official_metadata"):
        unknown.append("invalid_official_verification_metadata")
    if item["provenance"] == "UNKNOWN":
        unknown.append("observation_provenance_unknown")
    if item["known_author"]:
        adjustments.append(
            {"reason": "known_author_metadata", "value": POLICY["known_author_bonus"]}
        )
    else:
        unknown.append("author_metadata_unknown")
    if item["published_at"]:
        adjustments.append(
            {"reason": "known_publication_metadata", "value": POLICY["publication_metadata_bonus"]}
        )
    else:
        unknown.append("publication_metadata_unknown")
    if source_type == "official_advisory":
        if item["official_verified"] is True:
            adjustments.append(
                {"reason": "verified_advisory_metadata", "value": POLICY["verified_advisory_bonus"]}
            )
        else:
            unknown.append("official_verification_not_confirmed")
    if item["report_id"] and item["report_status"] == "accepted":
        adjustments.append({"reason": "accepted_report", "value": POLICY["accepted_report_bonus"]})
    quality = round(max(0.0, min(1.0, prior + sum(value["value"] for value in adjustments))), 6)
    blocked = []
    if item["publication_status"] not in POLICY["eligible_publication_statuses"]:
        blocked.append("publication_not_eligible")
    if item["sensitive"] or item["invalid_sensitivity"]:
        blocked.append("sensitivity_requires_review")
    if item["named_actors"]:
        blocked.append("named_actor_requires_review")
    if item["report_id"]:
        if item["report_status"] not in POLICY["eligible_report_statuses"]:
            blocked.append("report_moderation_not_eligible")
    elif source_type == "user_report":
        blocked.append("normalized_report_missing")
    if item["stance"] not in POLICY["eligible_stances"]:
        blocked.append("stance_not_corroborating")
    weight = 0.0 if blocked else round(quality * item["independence"], 6)
    return {
        "source_quality": quality,
        "weight": weight,
        "components": {
            "version": VERSION,
            "effective_source_type": source_type,
            "source_prior": prior,
            "provenance": item["provenance"],
            "adjustments": adjustments,
            "unknowns": unknown,
            "public_eligible": not blocked,
            "exclusion_reasons": blocked,
            "independence_score": item["independence"],
            "formula": "source_quality * independence when eligible",
            "report_status": item["report_status"],
            "publication_status": item["publication_status"],
            "stance": item["stance"],
        },
    }
