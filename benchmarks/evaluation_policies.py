"""PR36 deterministic policy scenarios over the application's production functions."""

from datetime import UTC, datetime, timedelta
from itertools import combinations

from app.confidence import score_confidence
from app.db.duplicates import _assess
from app.duplicates import compare
from app.evidence_weight import grade_evidence
from app.independence import score_evidence
from app.research import ResearchLimits
from app.scheduling import ResourceBudget, SchedulerPolicy, fits, priority, reservation, tier

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def evidence(index, **changes):
    stamp = (NOW + timedelta(days=index)).isoformat()
    return (
        dict(
            evidence_id=f"e{index}",
            claim_id=f"c{index}",
            document_id=f"d{index}",
            source_id=f"s{index}",
            group_id=None,
            author=f"author{index}",
            thread=f"thread{index}",
            platform="forum",
            urls=[f"https://example.com/{index}"],
            reference_urls=[],
            invalid_reference_count=0,
            cross_post_of=None,
            published_at=stamp,
            publication_basis="document",
            created_at=stamp,
            quote=f"Distinct short account {index}",
            normalized_claim="same report",
            url=f"https://example.com/{index}",
            content_hash=f"h{index}",
            clean_hash=None,
            location_id="city",
            occurred_at=None,
            stance="supports",
            vector=None,
        )
        | changes
    )


def contribution(index, **changes):
    return (
        dict(
            evidence_id=f"e{index}",
            claim_id=f"c{index}",
            weight=1.0,
            stance="supports",
            semantic_match_score=1.0,
            location_agreement="exact",
            source_type="forum",
            platform="forum",
        )
        | changes
    )


def candidate(**changes):
    return (
        dict(
            d24=0,
            d7=0,
            d30=1,
            last_demand=NOW,
            activity=0,
            last_run=NOW - timedelta(days=20),
            next_retry=None,
            busy=False,
            uncertain=False,
            incomplete=False,
            first_deep=False,
            new_hypothesis=False,
        )
        | changes
    )


def deterministic(case):
    task, inputs, expected = case["task"], case["input"], case["expected"]
    if task == "duplicates":
        left = evidence(0, **({"quote": inputs["default_quote"]} | inputs["left"]))
        right = evidence(1, **({"quote": inputs["default_quote"]} | inputs["right"]))
        observed = compare(left, right)
        return observed, {
            "classification": observed["classification"] == expected["classification"]
        }
    if task == "duplicate_groups":
        items = [
            evidence(i, **({"quote": inputs["default_quote"]} | changes))
            for i, changes in enumerate(inputs["items"])
        ]
        _, groups = _assess(items)
        observed = sorted(sorted(item["claim_id"] for item in group) for group in groups)
        return {"groups": observed}, {
            "groups": observed == sorted(sorted(group) for group in expected["groups"])
        }
    if task == "independence":
        items = [evidence(i, **changes) for i, changes in enumerate(inputs["items"])]
        pairs = {
            tuple(sorted((a["claim_id"], b["claim_id"]))): compare(a, b)
            for a, b in combinations(items, 2)
        }
        values = score_evidence(items, pairs)
        scores = [values[item["evidence_id"]]["score"] for item in items]
        return {"scores": scores}, {
            "credit_bounds": all(
                low <= score <= high
                for score, (low, high) in zip(scores, expected["bounds"], strict=True)
            )
        }
    if task == "confidence":
        scenario = inputs["scenario"]
        base = [contribution(0)]
        changes = {
            "independent": [contribution(i) for i in range(1, 5)],
            "copies": [contribution(i, weight=0) for i in range(1, 20)],
            "contradictions": [contribution(1, stance="contradicts")],
            "geography": [],
            "held": [],
        }[scenario]
        variant = base + changes
        if scenario == "geography":
            variant = [contribution(0, location_agreement="different")]
        if scenario == "held":
            held = grade_evidence(
                dict(
                    report_id=None,
                    source_type="blog",
                    provenance="UNKNOWN",
                    known_author=False,
                    published_at=None,
                    official_verified=False,
                    report_status=None,
                    publication_status="held",
                    sensitive=True,
                    invalid_sensitivity=False,
                    named_actors=False,
                    stance="supports",
                    independence=1.0,
                )
            )
            variant = base + [contribution(1, weight=held["weight"])]
        one = score_confidence(base, complete=True, review_reasons=[])["score"]
        two = score_confidence(variant, complete=True, review_reasons=[])["score"]
        relation = "greater" if two > one else "less" if two < one else "equal"
        return {"base": one, "variant": two, "relation": relation}, {
            "scenario": relation == expected["relation"]
        }
    if task == "scheduler":
        mode = inputs["scenario"]
        policy = SchedulerPolicy(enabled=True)
        routine = priority(candidate(), policy, NOW)
        observed = {}
        if mode == "hot":
            hot = priority(candidate(d24=10), policy, NOW)
            cold = priority(candidate(d30=0, last_demand=NOW - timedelta(days=40)), policy, NOW)
            passed = hot is not None and cold is None
            observed = {"hot": hot["priority"], "cold": cold}
        elif mode in {"uncertain", "deep"}:
            changes = (
                {"uncertain": True, "new_hypothesis": True}
                if mode == "uncertain"
                else {"first_deep": True, "incomplete": True}
            )
            special = priority(candidate(**changes), policy, NOW)
            passed = special["priority"] > routine["priority"]
            observed = {"special": special["priority"], "routine": routine["priority"]}
        elif mode == "budget":
            cost = reservation(ResearchLimits(queries=1, fetches=1, llm_calls=1))
            budget = ResourceBudget(jobs=2, queries=6, fetches=6, llm_calls=6)
            used = dict.fromkeys(cost, 0)
            admitted = 0
            for _ in range(100):
                if fits(used, cost, budget):
                    used = {key: used[key] + cost[key] for key in used}
                    admitted += 1
            violations = [
                fits(dict.fromkeys(cost, 0), cost, budget.model_copy(update={key: cost[key] - 1}))
                for key in cost
            ]
            passed = (
                admitted == 2
                and all(used[key] <= value for key, value in budget.model_dump().items())
                and not any(violations)
            )
            observed = {"admitted": admitted, "used": used, "caps": budget.model_dump()}
        elif mode == "decay":
            levels = [
                tier(candidate(d24=10), policy, NOW),
                tier(candidate(d7=3), policy, NOW),
                tier(candidate(), policy, NOW),
                tier(candidate(d30=0, last_demand=NOW - timedelta(days=40)), policy, NOW),
                tier(candidate(d30=0, last_demand=NOW - timedelta(days=100)), policy, NOW),
            ]
            # Last research activity also decays, rather than artificially keeping the item warm.
            levels[-1] = tier(
                candidate(
                    d30=0, last_demand=NOW - timedelta(days=100), last_run=NOW - timedelta(days=100)
                ),
                policy,
                NOW,
            )
            passed = levels == ["HOT", "ACTIVE", "NORMAL", "COLD", "DORMANT"]
            observed = {"tiers": levels}
        else:
            changes = (
                {"next_retry": NOW + timedelta(days=1)}
                if mode == "cooldown"
                else {"d30": 0, "last_demand": None, "last_run": None}
            )
            passed = priority(candidate(priority_adjustment=100, **changes), policy, NOW) is None
            observed = {"priority": None}
        return observed, {"scenario": passed}
    raise ValueError("unsupported_deterministic_task")
