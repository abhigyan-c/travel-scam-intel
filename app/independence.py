"""Versioned heuristic contribution credit; never a probability that a report is true."""

from datetime import datetime

VERSION = "independence-v1"
POLICY = {
    "unknown_author_base": 0.5,
    "identified_author_base": 0.75,
    "documented_origin_base": 1.0,
    "same_author_cap": 0.25,
    "same_thread_cap": 0.35,
    "same_origin_cap": 0.25,
    "quotation_overlap_cap": 0.35,
    "unassigned_duplicate_cap": 0.25,
    "explicit_reference_cap": 0.4,
    "cross_post_cap": 0.15,
    "invalid_reference_cap": 0.5,
    "same_platform_cap": 0.9,
    "publication_burst_cap": 0.8,
    "publication_window_hours": 6,
    "quotation_overlap_threshold": 0.8,
}


def _time(value):
    return datetime.fromisoformat(value) if value else None


def score_evidence(items: list[dict], pairs: dict) -> dict:
    ordered = sorted(
        items,
        key=lambda item: (
            bool(item["cross_post_of"]),
            _time(item["published_at"] or item["created_at"]),
            item["evidence_id"],
        ),
    )
    primary = {}
    for item in ordered:
        if item["group_id"]:
            primary.setdefault(item["group_id"], item["evidence_id"])
    scores, previous = {}, []
    for item in ordered:
        known = bool(item["platform"] and item["author"])
        documented = known and bool(item["thread"] or item["urls"]) and bool(item["published_at"])
        base = (
            POLICY["documented_origin_base"]
            if documented
            else POLICY["identified_author_base" if known else "unknown_author_base"]
        )
        caps, comparisons, unknown = [], [], []
        if not known:
            unknown.append("author_identity_unknown")
        if not item["thread"]:
            unknown.append("discussion_identity_unknown")
        if not item["published_at"]:
            unknown.append("publication_time_unknown")
        representative = primary.get(item["group_id"])
        if representative and representative != item["evidence_id"]:
            caps.append(
                {
                    "reason": "additional_duplicate_group_member",
                    "cap": 0.0,
                    "evidence_id": representative,
                }
            )
        if item["cross_post_of"]:
            caps.append({"reason": "declared_cross_post", "cap": POLICY["cross_post_cap"]})
        if item["invalid_reference_count"]:
            caps.append(
                {"reason": "invalid_reference_urls", "cap": POLICY["invalid_reference_cap"]}
            )
            unknown.append("some_reference_urls_unknown")
        referenced = []
        upstream_urls = set(item["reference_urls"]) - set(item["urls"])
        for other in ordered:
            if other["evidence_id"] == item["evidence_id"]:
                continue
            urls = sorted(upstream_urls & set(other["urls"]))
            if urls:
                referenced.append({"evidence_id": other["evidence_id"], "urls": urls})
        external = sorted(upstream_urls - {url for entry in referenced for url in entry["urls"]})
        if external:
            unknown.append("upstream_reference_outside_cohort")
        if upstream_urls:
            caps.append(
                {"reason": "explicit_upstream_reference", "cap": POLICY["explicit_reference_cap"]}
            )
        for other in previous:
            pair = pairs[tuple(sorted((item["claim_id"], other["claim_id"])))]
            signals = pair["signals"]
            same_platform = bool(item["platform"]) and item["platform"] == other["platform"]
            local_caps = []

            def add(reason, policy, other_id=other["evidence_id"], total=caps, local=local_caps):
                cap = {"reason": reason, "cap": POLICY[policy], "evidence_id": other_id}
                total.append(cap)
                local.append(cap)

            if item["source_id"] == other["source_id"] or set(item["urls"]) & set(other["urls"]):
                add("shared_publication_origin", "same_origin_cap")
            if signals["same_platform_author"]:
                add("shared_author", "same_author_cap")
            if signals["same_platform_thread"]:
                add("shared_discussion", "same_thread_cap")
            if (
                signals["substantial_quote"]
                and signals["quote_overlap"] >= POLICY["quotation_overlap_threshold"]
            ):
                add("large_quotation_overlap", "quotation_overlap_cap")
            if pair["classification"] == "duplicate" and (
                not item["group_id"] or item["group_id"] != other["group_id"]
            ):
                add("duplicate_pair_awaiting_group_review", "unassigned_duplicate_cap")
            gap = None
            if item["published_at"] and other["published_at"]:
                gap = (
                    abs(
                        (_time(item["published_at"]) - _time(other["published_at"])).total_seconds()
                    )
                    / 3600
                )
            if same_platform:
                add("shared_platform", "same_platform_cap")
                if gap is not None and gap <= POLICY["publication_window_hours"]:
                    add("nearby_publication_same_platform", "publication_burst_cap")
            comparisons.append(
                {
                    "evidence_id": other["evidence_id"],
                    "classification": pair["classification"],
                    "signals": signals,
                    "publication_gap_hours": gap,
                    "caps": local_caps,
                }
            )
        score = round(min([base, *[entry["cap"] for entry in caps]]), 6)
        scores[item["evidence_id"]] = {
            "score": score,
            "components": {
                "version": VERSION,
                "base_score": base,
                "caps": caps,
                "unknowns": unknown,
                "duplicate_group_id": item["group_id"],
                "group_representative": representative,
                "ordering_basis": "publication_time"
                if item["published_at"]
                else "claim_ingestion_time",
                "publication_basis": item["publication_basis"],
                "upstream_references": referenced,
                "external_upstream_urls": external,
                "cross_post_of": item["cross_post_of"],
                "comparisons": comparisons,
            },
        }
        previous.append(item)
    return scores
