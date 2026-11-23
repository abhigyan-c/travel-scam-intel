"""Fail-closed scoring for independently labeled, original synthetic examples."""

import hashlib
import json
from collections import Counter
from pathlib import Path

from app.extraction import ClaimBatch, GroundingError, validate_grounding
from app.relevance import Passage

TASKS = {
    "extraction",
    "location",
    "matching",
    "duplicates",
    "duplicate_groups",
    "independence",
    "confidence",
    "scheduler",
}
DATA = Path(__file__).parent / "data" / "36"


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def load_corpus(path=DATA / "corpus.json"):
    corpus = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        set(corpus) != {"version", "provenance", "locations", "cases"}
        or corpus["version"] != "evaluation-corpus-v1"
    ):
        raise ValueError("invalid_corpus_envelope")
    ids = [row["id"] for row in corpus["cases"]]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("duplicate_or_empty_cases")
    required = {
        "extraction": ({"document_id", "text"}, {"claims", "no_dates"}),
        "location": ({"text", "parent"}, {"status", "path"}),
        "matching": ({"claim", "location", "candidate"}, {"decision", "target", "stance"}),
        "duplicates": ({"left", "right", "default_quote"}, {"classification"}),
        "duplicate_groups": ({"items", "default_quote"}, {"groups"}),
        "independence": ({"items"}, {"bounds"}),
        "confidence": ({"scenario"}, {"relation"}),
        "scheduler": ({"scenario"}, {"passes"}),
    }
    for row in corpus["cases"]:
        if (
            set(row) != {"id", "task", "split", "input", "expected"}
            or row["task"] not in TASKS
            or row["split"] not in {"development", "holdout"}
        ):
            raise ValueError("invalid_case")
        inputs, expected = required[row["task"]]
        if set(row["input"]) != inputs or set(row["expected"]) != expected:
            raise ValueError("invalid_case_fields")
        if row["task"] == "scheduler" and (
            row["input"]["scenario"]
            not in {"hot", "uncertain", "deep", "budget", "decay", "cooldown", "dormant"}
            or row["expected"]["passes"] is not True
        ):
            raise ValueError("invalid_scheduler_scenario")
        if row["task"] == "extraction" and row["expected"]["no_dates"] is not True:
            raise ValueError("v1_corpus_only_contains_undated_examples")
        if row["task"] in {"independence", "duplicate_groups"}:
            if len(row["input"]["items"]) < 2:
                raise ValueError("vacuous_evidence_cohort")
            if row["task"] == "independence" and len(row["expected"]["bounds"]) != len(
                row["input"]["items"]
            ):
                raise ValueError("incomplete_independence_labels")
    for split in ["development", "holdout"]:
        if {row["task"] for row in corpus["cases"] if row["split"] == split} != TASKS:
            raise ValueError("missing_task_split")
    names = set()
    for location in corpus["locations"]:
        if (
            location["id"] in names
            or location["parent"] is not None
            and location["parent"] not in names
        ):
            raise ValueError("invalid_location_order_or_id")
        names.add(location["id"])
    for row in corpus["cases"]:
        if (
            row["task"] == "matching"
            and not {row["input"]["location"], row["input"]["candidate"]["location"]} <= names
        ):
            raise ValueError("unknown_matching_location")
        if (
            row["task"] == "location"
            and row["input"]["parent"] is not None
            and row["input"]["parent"] not in names
        ):
            raise ValueError("unknown_parent")
    fingerprint(corpus)  # Reject NaN/Infinity before execution.
    return corpus


def extraction_checks(case, claims, error):
    grounded = not error
    try:
        text = case["input"]["text"]
        validate_grounding(
            ClaimBatch.model_validate({"claims": claims}),
            Passage(index=0, start=0, end=len(text), text=text),
        )
    except (ValueError, GroundingError):
        grounded = False
    gold = case["expected"]["claims"]
    matched = set()
    tp = 0
    for claim in claims:
        for index, label in enumerate(gold):
            if index in matched:
                continue
            text = claim["claim"].casefold()
            mechanism = all(
                any(term.casefold() in text for term in group) for group in label["mechanism_terms"]
            )
            if grounded and mechanism and label["quote_fragment"] in claim["evidence_quote"]:
                matched.add(index)
                tp += 1
                break
    entities = [label["entity"] for label in gold if label["entity"]]
    checks = {
        "mechanism_claims": grounded and tp == len(gold) and len(claims) == len(gold),
        "no_invented_dates": not error and all(claim["occurred_at"] is None for claim in claims),
        "sensitivity_policy": grounded
        and all(
            any(entity in claim["entities"] and claim["sensitive"] for claim in claims)
            for entity in entities
        )
        and all(
            set(claim["entities"]) <= set(entities) and (claim["sensitive"] is bool(entities))
            for claim in claims
        ),
    }
    if not gold:
        checks["negative_safety"] = not error and not claims
    return {
        "true_positive": tp,
        "false_positive": len(claims) - tp,
        "false_negative": len(gold) - tp,
    }, checks


def fraction(passed, total, target=1.0):
    value = passed / total if total else None
    return {
        "passed": passed,
        "total": total,
        "value": value,
        "target": target,
        "meets_target": value is not None and value >= target,
    }


def scorecard(corpus, rows):
    expected = {row["id"]: row for row in corpus["cases"]}
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise ValueError("missing_duplicate_or_unknown_observations")
    for row in rows:
        case = expected[row["id"]]
        if (
            row["task"] != case["task"]
            or row["split"] != case["split"]
            or row["input_hash"] != fingerprint(case["input"])
        ):
            raise ValueError("observation_input_changed")
        if not row["checks"] or any(type(value) is not bool for value in row["checks"].values()):
            raise ValueError("invalid_check_values")
        if case["task"] == "extraction":
            counts, checks = extraction_checks(case, row["observed"]["claims"], row["error"])
            if counts != row["observed"]["counts"] or checks != row["checks"]:
                raise ValueError("extraction_scorecard_mismatch")
        if case["task"] in {"matching", "location"}:
            keys = (
                ["decision", "target", "stance"]
                if case["task"] == "matching"
                else ["status", "path"]
            )
            checks = {
                key: not row["error"] and row["observed"].get(key) == case["expected"][key]
                for key in keys
            }
            if checks != row["checks"]:
                raise ValueError("semantic_scorecard_mismatch")
    result = {}
    for split in ["all", "development", "holdout"]:
        selected = [row for row in rows if split == "all" or row["split"] == split]
        tasks = {
            task: fraction(
                sum(
                    not row["error"] and all(row["checks"].values())
                    for row in selected
                    if row["task"] == task
                ),
                sum(row["task"] == task for row in selected),
                0.75 if task in {"extraction", "matching"} else 1.0,
            )
            for task in sorted(TASKS)
        }
        extraction = [row for row in selected if row["task"] == "extraction"]
        counts = {
            key: sum(row["observed"]["counts"][key] for row in extraction)
            for key in ["true_positive", "false_positive", "false_negative"]
        }
        metrics = {
            "extraction_precision": fraction(
                counts["true_positive"], counts["true_positive"] + counts["false_positive"], 0.8
            ),
            "extraction_recall": fraction(
                counts["true_positive"], counts["true_positive"] + counts["false_negative"], 0.8
            ),
        }
        for check in ["negative_safety", "no_invented_dates", "sensitivity_policy"]:
            eligible = [row for row in extraction if check in row["checks"]]
            metrics[check] = fraction(sum(row["checks"][check] for row in eligible), len(eligible))
        matches = [row for row in selected if row["task"] == "matching"]
        negatives = [r for r in matches if expected[r["id"]]["expected"]["decision"] != "SAME"]
        positives = [r for r in matches if expected[r["id"]]["expected"]["decision"] == "SAME"]
        # Denominators include every label; missing/provider-error observations cannot disappear.
        metrics["no_false_merges"] = fraction(
            sum(not r["error"] and r["observed"]["decision"] != "SAME" for r in negatives),
            len(negatives),
        )
        metrics["no_false_splits"] = fraction(
            sum(not r["error"] and r["observed"]["decision"] == "SAME" for r in positives),
            len(positives),
        )
        metrics["stance_accuracy"] = fraction(
            sum(not r["error"] and r["checks"]["stance"] for r in matches), len(matches)
        )
        confusion = Counter(
            (expected[r["id"]]["expected"]["decision"], r["observed"].get("decision") or "ERROR")
            for r in matches
        )
        result[split] = {
            "tasks": tasks,
            "metrics": metrics,
            "matching_confusion": [
                {"expected": a, "observed": b, "count": n}
                for (a, b), n in sorted(confusion.items())
            ],
            "matching_errors": {
                "false_merges": sum(r["observed"].get("decision") == "SAME" for r in negatives),
                "distinct_opportunities": len(negatives),
                "false_splits": sum(
                    not r["error"] and r["observed"].get("decision") != "SAME" for r in positives
                ),
                "same_opportunities": len(positives),
                "unavailable": sum(bool(r["error"]) for r in matches),
            },
        }
    return result


def compare_baseline(current, baseline):
    if current["corpus_hash"] != baseline["corpus_hash"]:
        raise ValueError("baseline_labels_changed")
    old = {row["id"]: row for row in baseline["cases"]}
    new = {row["id"]: row for row in current["cases"]}
    if old.keys() != new.keys():
        raise ValueError("baseline_case_set_changed")
    regressions = []
    for identity, row in new.items():
        previous = old[identity]
        if row["input_hash"] != previous["input_hash"] or set(row["checks"]) != set(
            previous["checks"]
        ):
            raise ValueError("baseline_contract_changed")
        for check, was_passed in previous["checks"].items():
            if was_passed and not row["checks"][check]:
                regressions.append({"id": identity, "check": check})
    for split in current["scorecard"]:
        for name, metric in current["scorecard"][split]["metrics"].items():
            before = baseline["scorecard"][split]["metrics"][name]["value"]
            after = metric["value"]
            if before is not None and (after is None or after < before):
                regressions.append({"split": split, "metric": name})
    return {"passed": not regressions, "regressions": regressions}
