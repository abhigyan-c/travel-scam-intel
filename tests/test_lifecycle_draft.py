from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.db.hypothesis_lifecycle import Draft, Review, draft_from_claim


def test_draft_is_grounded_in_claim_summary_and_type():
    text = "An explicitly reported scam mechanism. " * 8
    draft = draft_from_claim(SimpleNamespace(claim_text=text, claim_type="transport"))
    assert len(draft.title) <= 120 and draft.title.endswith("...")
    assert draft.description == " ".join(text.split()) and draft.category == "transport"


@pytest.mark.parametrize(
    "values", [{"actor": " ", "reason": "checked"}, {"actor": "operator", "reason": ""}]
)
def test_empty_review_identity_or_reason_is_invalid(values):
    with pytest.raises(ValidationError):
        Review.model_validate(values)


def test_empty_generated_metadata_is_invalid():
    with pytest.raises(ValidationError):
        Draft(title="", description=" ", category="transport")
