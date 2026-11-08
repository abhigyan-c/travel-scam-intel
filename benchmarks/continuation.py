"""Controlled continuation comparison; no external services or provider charges."""

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

from app import relevance
from app.integrations.sarvam import Completion

TEXT = ("A traveler describes a journey through Fiji and a fixed fare agreement. " * 240).strip()


async def measure(classifier, *, durable):
    calls = 0
    saved = None
    rounds = []

    class Client:
        async def complete(self, **kwargs):
            nonlocal calls
            calls += 1
            # Controlled latency, not an estimate of Sarvam response time.
            await asyncio.sleep(0.01)
            return Completion(
                data=kwargs["schema"](
                    relevant=False, category=None, confidence=0.95, reason="Controlled negative"
                ),
                requests=1,
            )

    async def checkpoint(value):
        nonlocal saved
        saved = value

    start = time.perf_counter()
    for _ in range(3):
        before = calls
        result = await classifier(
            TEXT,
            Client(),
            max_passages=2,
            prior=saved,
            **({"checkpoint": checkpoint, "run_id": "controlled"} if durable else {}),
        )
        saved = result
        rounds.append(
            dict(
                new_calls=calls - before,
                retained_passages=len(result.assessments),
                complete=result.complete,
            )
        )
    return dict(calls=calls, elapsed_seconds=round(time.perf_counter() - start, 4), rounds=rounds)


async def run(args):
    if not re.fullmatch(r"[0-9a-f]{7,40}", args.baseline):
        raise ValueError("baseline must be a commit hash")
    source = subprocess.check_output(
        ["git", "show", f"{args.baseline}:app/relevance.py"], text=True
    )
    baseline = ModuleType("app._continuation_baseline")
    sys.modules[baseline.__name__] = baseline
    exec(compile(source, "baseline/relevance.py", "exec"), baseline.__dict__)
    old = await measure(baseline.classify_text, durable=False)
    new = await measure(relevance.classify_text, durable=True)
    assert not old["rounds"][-1]["complete"] and new["rounds"][-1]["complete"]
    report = dict(
        version="continuation-experiment-v1",
        baseline=args.baseline,
        baseline_source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        current_source_sha256=hashlib.sha256(
            Path(relevance.__file__).read_text().encode()
        ).hexdigest(),
        text_sha256=hashlib.sha256(TEXT.encode()).hexdigest(),
        text_characters=len(TEXT),
        legacy=old,
        continuation=new,
        limitations=[
            "Synthetic negative passages; not an extraction-quality benchmark.",
            "10ms controlled latency per call; timings are not live provider or VPS performance.",
            "The legacy run uses fewer calls because it stalls on its first two passages.",
            "Integration tests separately verify DB durability, reuse and budgets.",
        ],
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="9579f5e")
    parser.add_argument(
        "--output", type=Path, default=Path("benchmarks/data/31e/continuation.json")
    )
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(run(parser.parse_args()))
