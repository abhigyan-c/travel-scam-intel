import pytest

from app.hypothesis_resolution import public_new_candidate


def ordinary():
    return dict(
        location_id="resolved",
        data=dict(
            sensitive=False,
            entities=[],
            publication_status="unreviewed",
            evidence_quote="A sourced allegation.",
        ),
    )


@pytest.mark.parametrize("confidence", [0.1, 0.5, 0.95])
def test_low_match_certainty_does_not_hide_an_independent_candidate(confidence):
    assert public_new_candidate(
        dict(decision="NEW", target_id=None, ambiguous=False, confidence=confidence), ordinary()
    )


@pytest.mark.parametrize(
    "change",
    [
        dict(sensitive=True),
        dict(entities=["Named Business"]),
        dict(publication_status="held"),
        dict(evidence_quote=""),
    ],
)
def test_disclaimer_does_not_bypass_publication_restrictions(change):
    snapshot = ordinary()
    snapshot["data"].update(change)
    assert not public_new_candidate(dict(decision="NEW", target_id=None, ambiguous=False), snapshot)


@pytest.mark.parametrize(
    "decision",
    [
        dict(decision="NEW", target_id=None, ambiguous=True),
        dict(decision="SAME", target_id="existing", ambiguous=False),
    ],
)
def test_uncertain_or_existing_graph_decisions_still_need_review(decision):
    assert not public_new_candidate(decision, ordinary())
