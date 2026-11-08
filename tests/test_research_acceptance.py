import copy
import json
from pathlib import Path

import pytest

from benchmarks.acceptance import assess, live_checks, validate_controlled

DATA = Path("benchmarks/data/31f")


def report(name="controlled-live.json"):
    return json.loads((DATA / name).read_text())


def test_acceptance_keeps_failed_quality_targets_despite_reproducibility():
    result = assess(report(), report("controlled-replay.json"))
    assert result["checks"]["recording_reproduced"]
    assert not result["accepted"] and not result["checks"]["quality"]
    assert not result["checks"]["real_chatgpt"]
    assert result["controlled"]["scorecard"]["cold"]["positive_claim_coverage"]["total"] == 9


def test_missing_cases_cannot_improve_the_denominator():
    value = report()
    value["cases"].pop(0)
    with pytest.raises(ValueError, match="missing_or_duplicate"):
        validate_controlled(value)


def test_labels_cannot_be_changed_to_fit_the_recording():
    value = report()
    value["cases"][0]["expected"]["claim"] = False
    with pytest.raises(ValueError, match="labels_changed"):
        validate_controlled(value)


def test_altered_replay_counts_are_not_reproducibility():
    replay = report("controlled-replay.json")
    replay["cases"][0]["tokens"] += 1
    assert not assess(report(), replay)["checks"]["recording_reproduced"]


def trace():
    rows = []
    for name in ("Bangkok", "Suva"):
        rows.append(
            dict(
                location=name,
                public=dict(
                    items=[],
                    outcome="research_incomplete",
                    research=dict(complete=False, in_progress=False),
                ),
                progress=dict(
                    caps=dict(queries=6, fetches=20, documents=10, llm_calls=24),
                    reserved=dict(queries=6, llm_calls=24),
                    jobs=3,
                    seconds_reserved=135,
                    seconds_cap=135,
                ),
                jobs=[dict(status="succeeded") for _ in range(3)],
                public_evidence=[],
            )
        )
    rows[0]["public"].update(items=[{"id": "pattern"}], outcome="findings")
    rows[0]["public_evidence"].append(
        dict(
            from_current_root=True,
            verified=True,
            checks=dict(raw_hash=True, quote=True, not_held=True),
            source_url="https://example.org",
        )
    )
    return dict(observations=rows)


def test_partial_terminal_runs_can_be_traceable_but_wrong_empty_labels_fail():
    value = trace()
    assert live_checks(value)["passed"]
    value["observations"][1]["public"]["outcome"] = "nothing_found"
    assert not live_checks(value)["passed"]


@pytest.mark.parametrize("failure", ["quote", "not_held", "old_root", "no_evidence", "over_budget"])
def test_live_gate_requires_new_public_provenance_within_budget(failure):
    value = copy.deepcopy(trace())
    row = value["observations"][0]
    if failure in {"quote", "not_held"}:
        row["public_evidence"][0]["checks"][failure] = False
    elif failure == "old_root":
        row["public_evidence"][0]["from_current_root"] = False
    elif failure == "no_evidence":
        row["public_evidence"] = []
    else:
        row["progress"]["reserved"]["llm_calls"] = 25
    assert not live_checks(value)["passed"]
