import pytest

from app.db.resolution import normalize_entities


def test_entity_normalization_keeps_display_and_deduplicates_exact_names():
    assert normalize_entities(["  Acme   Tours ", "ＡＣＭＥ Tours", "Other business"]) == [
        {"name": "Acme Tours", "normalized_name": "acme tours"},
        {"name": "Other business", "normalized_name": "other business"},
    ]
    assert normalize_entities([]) == []
    assert len(normalize_entities(["Acme", "Acme Ltd."])) == 2


@pytest.mark.parametrize(
    "values", [None, "Acme", [None], [" "], ["x" * 201], ["hidden\u200bname"], ["x"] * 101]
)
def test_entity_input_bounds(values):
    with pytest.raises(ValueError):
        normalize_entities(values)
