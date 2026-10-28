import math

import pytest

from app.confidence import score_confidence


def item(index=0, **changes):
    return {
        "evidence_id": str(index),
        "claim_id": str(index),
        "weight": 1.0,
        "stance": "supports",
        "semantic_match_score": 1.0,
        "location_agreement": "exact",
        "source_type": "forum",
        "platform": "forum",
        **changes,
    }


def score(items, **kwargs):
    return score_confidence(items, complete=True, review_reasons=[], **kwargs)


def test_diminishing_returns_scenarios():
    scores = {n: score([item(i) for i in range(n)])["score"] for n in (0, 1, 5, 101, 105)}
    assert scores == {0: 0, 1: 0.266667, 5: 0.571429, 101: 0.784466, 105: 0.785047}
    assert scores[5] - scores[1] > 100 * (scores[105] - scores[101])


def test_copies_with_zero_independent_weight_cannot_boost_support_or_diversity():
    one = score([item()])
    copies = [item(i, weight=0, source_type="news", platform="new-site") for i in range(1, 105)]
    many = score([item(), *copies])
    assert one["score"] == many["score"]
    assert one["components"]["diversity_factor"] == many["components"]["diversity_factor"]


def test_contradictions_reduce_confidence_and_cannot_create_support():
    support = [item(i) for i in range(5)]
    scores = [
        score(support + [item(10 + i, stance="contradicts") for i in range(n)])["score"]
        for n in (0, 1, 5)
    ]
    assert scores == [0.571429, 0.444444, 0.235294]
    assert score([item(stance="contradicts")])["components"]["label"] == "no_support"


def test_semantic_and_location_agreement_are_separate_from_weight():
    assert score([item(semantic_match_score=0)])["score"] == 0
    assert score([item(location_agreement="different")])["score"] == 0
    known = score([item()])["score"]
    unknown = score([item(semantic_match_score=None, location_agreement="unknown")])
    assert 0 < unknown["score"] < known
    assert unknown["components"]["review_reasons"] == [
        "location_agreement_unknown",
        "semantic_agreement_unknown",
    ]


def test_source_diversity_is_modest_bounded_and_only_from_support():
    same = score([item(i) for i in range(5)])
    diverse = score([item(i, source_type=str(i), platform=str(i)) for i in range(5)])
    assert diverse["components"]["diversity_factor"] == 1
    assert diverse["score"] == 0.714286
    assert same["score"] < diverse["score"] <= same["score"] / 0.8 + 0.000001
    assert score([item(stance="contradicts", source_type="news")])["score"] == 0


def test_unrelated_uncertain_or_ineligible_evidence_cannot_corroborate():
    assert (
        score([item(stance="related"), item(1, stance="uncertain"), item(2, weight=0)])["score"]
        == 0
    )


def test_deterministic_order_and_partial_provenance_are_explicit():
    items = [item(1), item(2, weight=0.3)]
    assert score(items) == score(list(reversed(items)))
    result = score_confidence(
        items, complete=False, review_reasons=["suspected_duplicate", "suspected_duplicate"]
    )
    assert result["components"]["complete"] is False
    assert result["components"]["review_reasons"] == ["suspected_duplicate"]
    assert "incomplete" in result["explanation"]


@pytest.mark.parametrize("value", [-1, 1.1, math.nan, math.inf, True, "0.5"])
@pytest.mark.parametrize("field", ["weight", "semantic_match_score"])
def test_invalid_scores_fail_instead_of_becoming_confidence(value, field):
    with pytest.raises(ValueError, match="finite_unit_interval"):
        score([item(**{field: value})])
