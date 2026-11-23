"""Fail-closed PR 31F scorecard assembly; measurements never silently become passes."""

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import median

from benchmarks.research import fingerprint, load_corpus, scorecard


def validate_controlled(report):
    corpus = load_corpus()
    if report["corpus_hash"] != fingerprint(corpus):
        raise ValueError("acceptance_corpus_changed")
    expected = {row["id"]: row for row in corpus["cases"]}
    rows = report["cases"]
    for cache in ("cold", "warm"):
        selected = [r for r in rows if r["cache"] == cache]
        if len(selected) != len(expected) or {r["case"] for r in selected} != set(expected):
            raise ValueError("acceptance_missing_or_duplicate_cases")
        for row in selected:
            case = expected[row["case"]]
            if row["expected"] != case["expected"] or row["split"] != case["split"]:
                raise ValueError("acceptance_labels_changed")
    if len(rows) != 2 * len(expected):
        raise ValueError("acceptance_unexpected_cases")
    computed = scorecard(rows)
    if report["scorecard"] != computed:
        raise ValueError("acceptance_scorecard_mismatch")
    controls = all(
        row["incomplete_control_check"] is True
        for row in rows
        if expected[row["case"]]["fetch_error"] or not expected[row["case"]]["text"]
    )
    lineage = all(row["lineage_check"] is True for row in rows)
    quality = all(
        metric["meets_target"] for group in computed.values() for metric in group.values()
    )
    return dict(
        scorecard=computed,
        incomplete_controls_pass=controls,
        lineage_pass=lineage,
        quality_pass=quality,
    )


def replay_matches(live, replay):
    fields = (
        "case",
        "cache",
        "claims",
        "resolved_claims",
        "public_patterns",
        "complete",
        "grounded_claim_check",
        "geography_check",
        "public_check",
        "lineage_check",
        "incomplete_control_check",
        "llm_calls",
        "tokens",
        "usage_complete",
        "errors",
    )

    def outcomes(report):
        return sorted(
            ({key: row[key] for key in fields} for row in report["cases"]),
            key=lambda row: (row["case"], row["cache"]),
        )

    return outcomes(live) == outcomes(replay)


def live_checks(snapshot):
    observations = snapshot.get("observations", []) if snapshot else []
    locations = {row["location"]: row for row in observations}
    if set(locations) != {"Bangkok", "Suva"} or len(observations) != 2:
        return dict(
            passed=False, verified_evidence=0, terminal=False, budgets=False, outcomes=False
        )
    terminal, budgets, outcomes, verified = True, True, True, 0
    for row in observations:
        public, progress = row["public"], row["progress"]
        terminal &= (
            not public["research"]["in_progress"]
            and bool(row["jobs"])
            and all(job["status"] in {"succeeded", "failed"} for job in row["jobs"])
        )
        caps, used = progress.get("caps") or {}, progress.get("reserved") or {}
        budgets &= (
            set(caps) == {"queries", "fetches", "llm_calls", "documents"}
            and all(
                type(value) is int and 0 <= value <= caps.get(key, -1)
                for key, value in used.items()
            )
            and 1 <= (progress.get("jobs") or 0) <= 3
            and len(row["jobs"]) == progress.get("jobs")
            and 0 <= (progress.get("seconds_reserved") or 0) <= (progress.get("seconds_cap") or 0)
        )
        expected = (
            "findings"
            if public["items"]
            else ("nothing_found" if public["research"]["complete"] else "research_incomplete")
        )
        outcomes &= public["outcome"] == expected
        for evidence in row["public_evidence"]:
            verified += int(
                evidence.get("from_current_root") is True
                and evidence.get("verified") is True
                and bool(evidence.get("checks"))
                and all(value is True for value in evidence["checks"].values())
                and bool(evidence.get("source_url"))
            )
    return dict(
        passed=bool(terminal and budgets and outcomes and verified),
        verified_evidence=verified,
        terminal=bool(terminal),
        budgets=bool(budgets),
        outcomes=bool(outcomes),
    )


def live_measurements(snapshot):
    results = {}
    for row in (snapshot or {}).get("observations", []):
        jobs, progress = row["jobs"], row["progress"]
        stages = Counter()
        calls = []
        for job in jobs:
            for timing in (job.get("diagnostics") or {}).get("stage_timings", []):
                stages[timing["stage"]] += timing["seconds"]
            calls.extend((job.get("usage") or {}).get("llm", []))
        span = (
            max(datetime.fromisoformat(j["updated_at"]) for j in jobs)
            - min(datetime.fromisoformat(j["created_at"]) for j in jobs)
        ).total_seconds()
        results[row["location"]] = dict(
            root_span_seconds=round(span, 3),
            span_includes_queue_and_finish=True,
            reservations=progress["reserved"],
            stage_seconds={stage: round(seconds, 3) for stage, seconds in stages.items()},
            reported_tokens=sum(c.get("usage", {}).get("total_tokens", 0) for c in calls),
            usage_complete=len(calls) == progress["reserved"].get("llm_calls", 0)
            and all(bool(c.get("usage")) for c in calls),
            public_patterns=row["public"]["total"],
            localhost_rest_median_seconds=median(r["seconds"] for r in row["localhost_rest_reads"])
            if row.get("localhost_rest_reads")
            else None,
        )
    return results


def assess(controlled, replay, *, live=None, chatgpt=None):
    measured = validate_controlled(controlled)
    validate_controlled(replay)
    reproduced = replay_matches(controlled, replay)
    # A live trace needs actual public, verified provenance, not just an empty successful RPC.
    trace = live_checks(live)
    live_pass = trace["passed"]
    chatgpt_pass = bool(
        chatgpt
        and chatgpt.get("user_confirmed") is True
        and chatgpt.get("read_and_followup_pass") is True
        and chatgpt.get("evidence_and_scope_pass") is True
    )
    checks = dict(
        quality=measured["quality_pass"],
        lineage=measured["lineage_pass"],
        incomplete_controls=measured["incomplete_controls_pass"],
        recording_reproduced=reproduced,
        deployed_live_trace=live_pass,
        real_chatgpt=chatgpt_pass,
    )
    return dict(
        version="research-acceptance-v1",
        accepted=all(checks.values()),
        checks=checks,
        controlled=measured,
        live=trace,
        live_measurements=live_measurements(live),
        blockers=[name for name, passed in checks.items() if not passed],
        limitations=[
            "Visible synthetic corpus; not an independent holdout or global reliability estimate.",
            "A connector/protocol call is not a substitute for the user's real ChatGPT test.",
            "Unknown provider billing/latency remains unknown; replay timing is not live speed.",
        ],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controlled", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--live", type=Path)
    parser.add_argument("--chatgpt", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args()

    def read(path):
        return json.loads(path.read_text()) if path else None

    report = assess(
        read(args.controlled), read(args.replay), live=read(args.live), chatgpt=read(args.chatgpt)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"accepted": report["accepted"], "blockers": report["blockers"]}))
    if args.require_pass and not report["accepted"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
