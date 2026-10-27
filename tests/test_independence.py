from datetime import UTC, datetime, timedelta
from itertools import combinations

import pytest

from app.duplicates import compare
from app.independence import score_evidence


def item(index, **changes):
    published = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=index)
    return {
        "evidence_id": f"e{index}",
        "claim_id": f"c{index}",
        "document_id": f"d{index}",
        "source_id": f"s{index}",
        "group_id": None,
        "author": f"user{index}",
        "thread": f"thread{index}",
        "platform": "forum",
        "urls": [f"https://example.org/{index}"],
        "reference_urls": [],
        "invalid_reference_count": 0,
        "cross_post_of": None,
        "published_at": published.isoformat(),
        "publication_basis": "document",
        "created_at": published.isoformat(),
        "quote": f"Distinct short account {index}",
        "normalized_claim": "reported inflated fares",
        "url": f"https://example.org/{index}",
        "content_hash": f"h{index}",
        "clean_hash": None,
        "location_id": "city",
        "occurred_at": None,
        "stance": "supports",
        "vector": None,
        **changes,
    }


def calculate(items):
    pairs = {
        tuple(sorted((a["claim_id"], b["claim_id"]))): compare(a, b)
        for a, b in combinations(items, 2)
    }
    return score_evidence(items, pairs)


def test_unknown_authors_are_uncertain_not_assumed_independent():
    scores = calculate([item(0, author=None), item(1, author=None)])
    assert [value["score"] for value in scores.values()] == [0.5, 0.5]
    assert all(
        "author_identity_unknown" in value["components"]["unknowns"] for value in scores.values()
    )


def test_identified_author_without_publication_time_has_partial_base():
    assert calculate([item(0, published_at=None)])["e0"]["score"] == 0.75


def test_documented_distinct_platform_origins_receive_unpenalized_credit():
    scores = calculate([item(0), item(1, platform="other")])
    assert scores["e0"]["score"] == scores["e1"]["score"] == 1


def test_shared_author_is_capped_even_when_wording_is_not_a_copy():
    scores = calculate([item(0, author="same"), item(1, author="same")])
    assert scores["e1"]["score"] == 0.25
    assert any(cap["reason"] == "shared_author" for cap in scores["e1"]["components"]["caps"])


def test_same_handle_on_different_platforms_is_not_same_identity():
    scores = calculate([item(0, author="same"), item(1, author="same", platform="other")])
    assert scores["e1"]["score"] == 1


def test_shared_discussion_limits_credit_from_different_authors():
    assert calculate([item(0, thread="same"), item(1, thread="same")])["e1"]["score"] == 0.35


def test_distinct_claims_from_one_publication_are_not_fully_independent():
    assert calculate([item(0, source_id="same"), item(1, source_id="same")])["e1"]["score"] == 0.25


def test_publication_burst_is_only_a_mild_correlation_signal():
    a, b = item(0), item(1, published_at=item(0)["published_at"])
    assert calculate([a, b])["e1"]["score"] == 0.8
    b["published_at"] = None
    score = calculate([a, b])["e1"]
    assert score["score"] == 0.75
    assert score["components"]["comparisons"][0]["publication_gap_hours"] is None


def test_same_platform_without_a_burst_has_a_soft_cap():
    assert calculate([item(0), item(1)])["e1"]["score"] == 0.9


def test_copy_group_contributes_at_most_one_representative():
    scores = calculate([item(index, group_id="copies") for index in range(4)])
    assert sum(value["score"] for value in scores.values()) == 1
    assert [scores[f"e{index}"]["score"] for index in range(4)] == [1, 0, 0, 0]


def test_declared_copy_is_not_preferred_over_its_original_when_dates_are_missing():
    original = item(1, group_id="copies", published_at=None)
    copied = item(0, group_id="copies", cross_post_of=original["urls"][0], published_at=None)
    scores = calculate([copied, original])
    assert scores["e1"]["score"] == 0.75 and scores["e0"]["score"] == 0
    assert scores["e0"]["components"]["group_representative"] == "e1"


def test_reference_to_a_later_ordered_report_still_reduces_citing_report_credit():
    citing = item(0, reference_urls=item(1)["urls"])
    scores = calculate([citing, item(1)])
    assert scores["e0"]["score"] == 0.4
    assert scores["e0"]["components"]["upstream_references"][0]["evidence_id"] == "e1"


def test_unknown_external_upstream_reference_has_partial_credit():
    value = calculate([item(0, reference_urls=["https://elsewhere.org/report"])])["e0"]
    assert (
        value["score"] == 0.4
        and "upstream_reference_outside_cohort" in value["components"]["unknowns"]
    )


def test_link_to_current_publication_does_not_create_fake_upstream_dependence():
    current = item(0, reference_urls=item(0)["urls"])
    assert calculate([current])["e0"]["score"] == 1


def test_declared_crosspost_without_available_original_is_not_independent():
    assert calculate([item(0, cross_post_of="https://elsewhere.org/report")])["e0"]["score"] == 0.15


def test_invalid_reference_metadata_preserves_uncertainty():
    assert calculate([item(0, invalid_reference_count=1)])["e0"]["score"] == 0.5


def test_semantic_similarity_alone_does_not_reduce_independence():
    scores = calculate([item(0, vector=[1.0, 0.0]), item(1, vector=[1.0, 0.0], platform="other")])
    assert scores["e1"]["score"] == 1


def test_quotation_overlap_is_partial_dependence_not_a_hard_duplicate():
    quote = (
        "This is a substantial passage describing a reported fare problem with many specific "
        "details copied between sources for review of the underlying report."
    )
    scores = calculate([item(0, quote=quote, author=None), item(1, quote=quote, author=None)])
    assert scores["e1"]["score"] == 0.35


def test_policy_uses_strongest_cap_instead_of_multiplying_correlated_signals():
    scores = calculate([item(0, author="u", thread="t"), item(1, author="u", thread="t")])
    assert scores["e1"]["score"] == 0.25


def test_order_and_results_are_repeatable_with_every_score_in_range():
    items = [
        item(index, author=None if index % 2 else "u", group_id="copies" if index < 3 else None)
        for index in range(8)
    ]
    forward, reverse = calculate(items), calculate(list(reversed(items)))
    assert forward == reverse
    assert all(0 <= value["score"] <= 1 for value in forward.values())


@pytest.mark.parametrize("items", [[], [item(0, author=None, published_at=None)]])
def test_empty_and_singleton_cohorts_are_valid(items):
    assert len(calculate(items)) == len(items)
