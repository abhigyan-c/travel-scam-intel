import json
from typing import get_args

from app.extraction import SYSTEM_PROMPT, source_bound_schema
from app.relevance import Passage
from benchmarks.extraction_diagnostics import MINIMAL_ADDENDUM, legacy_schema
from benchmarks.research import DATA


def test_promoted_prompt_matches_the_evaluated_minimal_candidate():
    old = json.loads((DATA / "31c/protocol-v2.json").read_text())["system"]
    assert SYSTEM_PROMPT == old + MINIMAL_ADDENDUM


def test_distinct_line_article_exposes_old_quote_exclusion_and_new_coverage():
    case = json.loads((DATA / "31c/article-cases-v2.json").read_text())["cases"][0]
    passage = Passage(index=0, start=0, end=len(case["text"]), text=case["text"])
    quotes = []
    for schema in (legacy_schema(passage), source_bound_schema(passage)):
        claim = schema.model_fields["claims"].annotation.__args__[0]
        quotes.append(get_args(claim.model_fields["evidence_quote"].annotation))
    term = case["expected"]["evidence_terms"][0]
    assert not any(term in quote for quote in quotes[0])
    assert any(term in quote for quote in quotes[1])


def test_saved_screens_justify_rejection_and_preserve_negative_controls():
    screen = json.loads((DATA / "31c/development-screen.json").read_text())["rows"]
    for variant in ("instructions", "evidence-first"):
        assert any(
            r["variant"] == variant and not r["expected"]["claim"] and r["claims"] for r in screen
        )
    for filename in ("minimal-screen.json", "holdout.json"):
        rows = json.loads((DATA / "31c" / filename).read_text())["rows"]
        assert all(r["supported"] if r["expected"]["claim"] else r["claims"] == 0 for r in rows)
        assert all(not r["invalid_fields"] for r in rows)
