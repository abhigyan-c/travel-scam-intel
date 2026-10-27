import pytest

from app.evidence_weight import POLICY, grade_evidence


def item(**changes):
    return {
        "source_type": "forum",
        "provenance": "FIRST_HAND",
        "known_author": False,
        "published_at": None,
        "official_verified": None,
        "report_id": None,
        "report_status": None,
        "publication_status": "unreviewed",
        "sensitive": False,
        "invalid_sensitivity": False,
        "named_actors": False,
        "stance": "supports",
        "independence": 0.5,
        **changes,
    }


@pytest.mark.parametrize(
    "provenance,quality",
    [("FIRST_HAND", 0.65), ("WITNESSED", 0.6), ("SECOND_HAND", 0.4), ("UNKNOWN", 0.45)],
)
def test_provenance_adjustments_and_explicit_weight_formula(provenance, quality):
    grade = grade_evidence(item(provenance=provenance))
    assert grade["source_quality"] == quality
    assert grade["weight"] == round(quality * 0.5, 6)


@pytest.mark.parametrize("source_type", POLICY["source_priors"])
def test_source_priors_are_deterministic_and_bounded(source_type):
    grade = grade_evidence(item(source_type=source_type))
    assert grade["source_quality"] == round(min(1, POLICY["source_priors"][source_type] + 0.15), 6)
    assert 0 <= grade["weight"] <= grade["source_quality"] <= 1


def test_anonymous_processed_report_contributes_without_named_identity():
    grade = grade_evidence(
        item(source_type="user_report", report_id="report", report_status="processed")
    )
    assert grade["source_quality"] == 0.65 and grade["weight"] == 0.325
    assert grade["components"]["public_eligible"]


def test_linked_report_cannot_get_an_official_source_prior():
    grade = grade_evidence(
        item(
            source_type="official_advisory",
            official_verified=True,
            report_id="report",
            report_status="processed",
        )
    )
    assert grade["source_quality"] == 0.65
    assert grade["components"]["effective_source_type"] == "user_report"


def test_verified_advisory_bonus_is_explicit_and_scores_clamp_at_one():
    grade = grade_evidence(
        item(
            source_type="official_advisory",
            official_verified=True,
            known_author=True,
            published_at="known",
            independence=1,
        )
    )
    assert grade["source_quality"] == grade["weight"] == 1
    other = grade_evidence(item(source_type="other", official_verified=True))
    assert other["source_quality"] == grade_evidence(item(source_type="other"))["source_quality"]


def test_metadata_bonuses_and_accepted_report_bonus_are_separate_from_truth():
    grade = grade_evidence(
        item(report_id="report", report_status="accepted", known_author=True, published_at="known")
    )
    assert grade["source_quality"] == 0.8
    assert {entry["reason"] for entry in grade["components"]["adjustments"]} == {
        "report_provenance",
        "known_author_metadata",
        "known_publication_metadata",
        "accepted_report",
    }


@pytest.mark.parametrize("status", ["pending", "needs_review", "rejected"])
def test_unready_reports_have_zero_public_weight(status):
    grade = grade_evidence(item(report_id="report", report_status=status))
    assert grade["source_quality"] > 0 and grade["weight"] == 0
    assert not grade["components"]["public_eligible"]


@pytest.mark.parametrize(
    "changes",
    [
        {"publication_status": "held"},
        {"publication_status": "rejected"},
        {"publication_status": "unknown"},
        {"sensitive": True},
        {"named_actors": True},
        {"invalid_sensitivity": True},
    ],
)
def test_holds_and_unknown_publication_state_cannot_gain_weight(changes):
    grade = grade_evidence(item(**changes))
    assert grade["weight"] == 0 and grade["components"]["exclusion_reasons"]


def test_plain_approval_marker_cannot_release_sensitive_or_named_actor_allegations():
    assert grade_evidence(item(publication_status="approved", sensitive=True))["weight"] == 0
    assert grade_evidence(item(publication_status="approved", named_actors=True))["weight"] == 0


def test_missing_normalized_user_report_is_ineligible():
    grade = grade_evidence(item(source_type="user_report"))
    assert (
        grade["weight"] == 0
        and "normalized_report_missing" in grade["components"]["exclusion_reasons"]
    )


@pytest.mark.parametrize("stance", ["related", "uncertain"])
def test_noncorroborating_stances_do_not_receive_aggregate_weight(stance):
    assert grade_evidence(item(stance=stance))["weight"] == 0


def test_contradiction_has_nonnegative_weight_for_separate_downstream_stance_handling():
    assert grade_evidence(item(stance="contradicts"))["weight"] == 0.325


def test_duplicate_zero_credit_stays_zero_despite_high_quality():
    grade = grade_evidence(
        item(source_type="official_advisory", official_verified=True, independence=0)
    )
    assert grade["components"]["public_eligible"] and grade["weight"] == 0
