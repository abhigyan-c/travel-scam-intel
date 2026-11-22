import copy
import json
from types import SimpleNamespace

import pytest

from benchmarks.evaluate import SEMANTIC_FILES, run, sources
from benchmarks.evaluation_metrics import (
    DATA,
    compare_baseline,
    extraction_checks,
    fingerprint,
    fraction,
    load_corpus,
    scorecard,
)
from benchmarks.evaluation_policies import deterministic


@pytest.fixture
def baseline():
    return json.loads((DATA / "baseline.json").read_text(encoding="utf-8"))


def changed(report, id, **observed):
    row = next(row for row in report["cases"] if row["id"] == id)
    row["observed"].update(observed)
    case = next(case for case in load_corpus()["cases"] if case["id"] == id)
    row["checks"] = {
        key: row["observed"][key] == case["expected"][key]
        for key in ["decision", "target", "stance"]
    }
    return row


def test_every_task_has_independent_labels_in_both_partitions(baseline):
    corpus = load_corpus()
    assert len(corpus["cases"]) == 49
    assert fingerprint(corpus) == baseline["corpus_hash"]
    assert scorecard(corpus, baseline["cases"]) == baseline["scorecard"]
    assert baseline["quality_passed"] is True
    assert fraction(0, 0)["value"] is None and not fraction(0, 0)["meets_target"]


@pytest.mark.parametrize("mutation", ["drop", "duplicate", "unknown", "input", "split"])
def test_missing_duplicate_or_misattributed_observations_cannot_pass(baseline, mutation):
    rows = copy.deepcopy(baseline["cases"])
    if mutation == "drop":
        rows.pop()
    elif mutation == "duplicate":
        rows[-1] = copy.deepcopy(rows[0])
    elif mutation == "unknown":
        rows[-1]["id"] = "not-in-corpus"
    elif mutation == "input":
        rows[-1]["input_hash"] = "0" * 64
    else:
        rows[-1]["split"] = "development"
    with pytest.raises(ValueError):
        scorecard(load_corpus(), rows)


def test_aggregate_accuracy_cannot_hide_a_new_false_merge(baseline):
    # Construct the reference defect explicitly so future legitimate improvements
    # do not require preserving today's model mistake to keep this metric test green.
    baseline = copy.deepcopy(baseline)
    changed(baseline, "broader-not-subtype", decision="SUBTYPE")
    changed(baseline, "different-trick", decision="RELATED")
    baseline["scorecard"] = scorecard(load_corpus(), baseline["cases"])
    candidate = copy.deepcopy(baseline)
    changed(candidate, "broader-not-subtype", decision="RELATED")
    changed(candidate, "different-trick", decision="SAME")
    candidate["scorecard"] = scorecard(load_corpus(), candidate["cases"])
    assert (
        candidate["scorecard"]["all"]["tasks"]["matching"]["value"]
        == baseline["scorecard"]["all"]["tasks"]["matching"]["value"]
    )
    assert candidate["scorecard"]["all"]["matching_errors"]["false_merges"] == 1
    assert not compare_baseline(candidate, baseline)["passed"]


def test_false_split_and_provider_failure_remain_in_denominators(baseline):
    candidate = copy.deepcopy(baseline)
    changed(candidate, "same-meter", decision="NEW", target=None)
    card = scorecard(load_corpus(), candidate["cases"])
    assert card["all"]["matching_errors"]["false_splits"] == 1
    assert card["all"]["metrics"]["no_false_splits"]["value"] == 0.5
    row = next(row for row in candidate["cases"] if row["id"] == "same-paraphrase")
    row["error"] = "semantic_provider_failed"
    row["observed"]["decision"] = None
    row["checks"] = dict.fromkeys(row["checks"], False)
    card = scorecard(load_corpus(), candidate["cases"])
    assert card["all"]["metrics"]["no_false_splits"]["total"] == 2
    assert card["all"]["matching_errors"]["unavailable"] == 1
    assert not card["all"]["metrics"]["no_false_splits"]["meets_target"]


def test_fabricated_claims_and_forged_extraction_summaries_do_not_score(baseline):
    corpus = load_corpus()
    case = corpus["cases"][0]
    row = copy.deepcopy(baseline["cases"][0])
    row["observed"]["claims"][0]["evidence_quote"] = (
        "A fabricated source quote demanded extra money."
    )
    counts, checks = extraction_checks(case, row["observed"]["claims"], None)
    assert counts["true_positive"] == 0 and counts["false_negative"] == 1
    assert checks["mechanism_claims"] is False
    rows = copy.deepcopy(baseline["cases"])
    rows[0] = row
    with pytest.raises(ValueError, match="extraction_scorecard_mismatch"):
        scorecard(corpus, rows)


def test_labels_must_not_be_silently_rebased(baseline):
    candidate = copy.deepcopy(baseline)
    candidate["corpus_hash"] = "f" * 64
    with pytest.raises(ValueError, match="labels_changed"):
        compare_baseline(candidate, baseline)


def test_deterministic_scenarios_use_current_production_policy(monkeypatch):
    import benchmarks.evaluation_policies as policies
    from app.independence import POLICY

    corpus = load_corpus()
    case = next(c for c in corpus["cases"] if c["id"] == "same-author")
    assert deterministic(case)[1]["credit_bounds"]
    monkeypatch.setitem(POLICY, "same_author_cap", 1.0)
    assert deterministic(case)[1]["credit_bounds"] is False
    case = next(c for c in corpus["cases"] if c["id"] == "cycle-all-dimensions")
    monkeypatch.setattr(policies, "fits", lambda *args: True)
    assert deterministic(case)[1]["scenario"] is False


async def test_prompt_drift_fails_before_database_or_provider_access(tmp_path, monkeypatch):
    recording = json.loads((DATA / "sarvam-recording.json").read_text(encoding="utf-8"))
    assert recording["semantic_source_hash"] == fingerprint(sources(SEMANTIC_FILES))
    recording["semantic_source_hash"] = "changed"
    path = tmp_path / "recording.json"
    path.write_text(json.dumps(recording), encoding="utf-8")
    import benchmarks.evaluate as module

    def forbidden(*args, **kwargs):
        pytest.fail("stale recording attempted database access")

    monkeypatch.setattr(module, "isolated_database", forbidden)
    args = SimpleNamespace(
        corpus=DATA / "corpus.json", mode="replay", recording=path, output=tmp_path / "report.json"
    )
    with pytest.raises(ValueError, match="recording_inputs_or_semantic_sources_changed"):
        await run(args)


def test_execution_failure_replaces_stale_report_without_exposing_exception(
    tmp_path, monkeypatch, capsys
):
    import benchmarks.evaluate as module

    output = tmp_path / "report.json"
    output.write_text('{"execution_passed":true}', encoding="utf-8")

    async def failed(args):
        raise ValueError("private-credential-must-never-be-printed")

    monkeypatch.setattr(module, "run", failed)
    monkeypatch.setattr("sys.argv", ["evaluate", "--output", str(output)])
    with pytest.raises(SystemExit) as result:
        module.main()
    assert result.value.code == 2
    assert json.loads(output.read_text())["execution_passed"] is False
    assert "private-credential" not in capsys.readouterr().out
