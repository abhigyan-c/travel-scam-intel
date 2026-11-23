"""Controlled acquisition comparison, plus an explicitly bounded live source probe."""

import argparse
import asyncio
import hashlib
import json
import subprocess
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from app.acquisition import AcquisitionPolicy, dedup_key, diversify, host_key
from app.core.config import Settings
from app.discovery import discover, generate_queries
from app.integrations.fetch import Fetcher, FetchError
from app.integrations.serper import Serper
from app.processing import process_bytes

DATA = Path(__file__).parent / "data/31d"


def simulate(case, *, revised):
    pages = [SimpleNamespace(**p) for p in case["pages"]]
    if revised:
        seen = set()
        pages = [p for p in pages if not (dedup_key(p.url) in seen or seen.add(dedup_key(p.url)))]
        pages = diversify(pages)
    policy = AcquisitionPolicy()
    elapsed, attempted, resources, errors = 0.0, 0, set(), Counter()
    examined = 0
    for page in pages:
        if attempted >= case["fetch_limit"] or elapsed >= case["time_limit"]:
            break
        examined += 1
        if revised and not policy.allow(page.url):
            continue
        attempted += 1
        duration = min(page.seconds, 6) if revised else page.seconds
        if elapsed + duration > case["time_limit"]:
            elapsed = case["time_limit"]
            errors["deadline"] += 1
            break
        elapsed += duration
        error = "acquisition_timeout" if revised and page.seconds > 6 else page.error
        if error or page.status != 200:
            code = error or ("rate_limited" if page.status == 429 else "http_status")
            errors[code] += 1
            if revised:
                policy.failure(page.url, FetchError(code, status=page.status))
            continue
        policy.acquired(page.url)
        policy.processed(page.url, page.outcome)
        if page.outcome == "ready":
            resources.add(page.resource)
        elif page.outcome == "empty":
            errors["empty"] += 1
    return {
        "fetch_attempts": attempted,
        "unique_readable_documents": len(resources),
        "virtual_seconds": elapsed,
        "errors": dict(errors),
        "deferred": policy.diagnostics["deferred"],
        "acquisition_complete": examined == len(pages)
        and not errors
        and not policy.diagnostics["deferred"],
    }


async def probe(args):
    if not args.live:
        raise ValueError("explicit_live_flag_required")

    class TimedSearch:
        def __init__(self):
            self.provider, self.timings = Serper(Settings().serper_api_key), []

        async def search(self, query):
            started = time.monotonic()
            response = await self.provider.search(query)
            self.timings.append(
                {
                    "query_index": generate_queries(args.location).index(query),
                    "query": query,
                    "seconds": round(time.monotonic() - started, 3),
                    "usage": response.usage.model_dump(),
                }
            )
            return response

    provider = TimedSearch()
    started = time.monotonic()
    found = await discover(args.location, provider, max_queries=3, max_results=30, timeout=25)
    discovery_seconds = time.monotonic() - started
    policy, observations = AcquisitionPolicy(), []
    fetcher = Fetcher(timeout=6)
    deadline = time.monotonic() + 60
    for candidate in found.results:
        if len(observations) >= 8 or time.monotonic() >= deadline:
            break
        if not policy.allow(candidate.url):
            continue
        began = time.monotonic()
        row = {"url": candidate.url, "host": host_key(candidate.url)}
        try:
            page = await fetcher.fetch(candidate.url, host_allowed=policy.permit_redirect)
            policy.acquired(page.url)
            processed = await asyncio.to_thread(
                process_bytes, page.content, content_type=page.content_type, url=page.url
            )
            policy.processed(page.url, processed.outcome)
            row.update(
                outcome=processed.outcome,
                language=processed.language,
                bytes=len(page.content),
                raw_hash=hashlib.sha256(page.content).hexdigest(),
            )
        except FetchError as exc:
            policy.failure(candidate.url, exc)
            row.update(error=str(exc), http_status=exc.status)
        row["seconds"] = round(time.monotonic() - began, 3)
        observations.append(row)
        print(f"{row['host']}: {row.get('outcome', row.get('error'))}", flush=True)
    return {
        "kind": "single_live_source_probe",
        "location": args.location,
        "discovery_seconds": round(discovery_seconds, 3),
        "queries": provider.timings,
        "candidates": len(found.results),
        "observations": observations,
        "policy": policy.diagnostics,
        "limitations": [
            "One changing live sample; no before/after speed claim.",
            "At most three searches and eight fetches, each bounded to six seconds.",
            "No LLM, database writes, object storage writes or scam findings.",
            "Bodies/text are not retained; ready is processable text, not useful evidence.",
        ],
    }


async def run(args):
    if args.output.exists():
        raise ValueError("choose_new_output_path")
    if args.live:
        report = await probe(args)
    else:
        corpus = json.loads((DATA / "acquisition-cases.json").read_text())
        report = {
            "kind": "controlled_virtual_acquisition",
            "corpus_version": corpus["version"],
            "results": [
                {
                    "case": c["id"],
                    "baseline": simulate(c, revised=False),
                    "revised": simulate(c, revised=True),
                }
                for c in corpus["cases"]
            ],
            "limitations": [
                "Virtual timings are fixture assumptions, not measured website latency.",
                "Logical fetch attempts are not individual redirect/retry HTTP requests.",
                "Includes a late-recovery case where deferral loses an otherwise readable page.",
            ],
        }
    report["commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--location", default="Bangkok, Thailand")
    args = parser.parse_args()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(run(args))


if __name__ == "__main__":
    main()
