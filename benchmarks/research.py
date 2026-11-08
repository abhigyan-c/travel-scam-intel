"""Replay or capture bounded Sarvam research against synthetic sources and local PostgreSQL."""

import argparse
import asyncio
import hashlib
import io
import json
import re
import subprocess
import time
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.acquisition import POLICY_VERSION
from app.core.config import Settings
from app.db.claim_pipeline import apply_and_score_claim
from app.db.resolution import RESOLUTION_VERSION, get_location_path, normalize_name
from app.db.session import Base
from app.discovery import DISCOVERY_VERSION, SearchResponse, SearchResult, SearchUsage
from app.embeddings import Embedder
from app.extraction import PROMPT_VERSION as EXTRACTION_VERSION
from app.integrations.fetch import FetchedPage, FetchError
from app.integrations.sarvam import MODEL, Completion, Sarvam
from app.models import Claim, ClaimExtraction, Document, Location, Source
from app.processing import PROCESSING_VERSION
from app.relevance import PROMPT_VERSION as RELEVANCE_VERSION
from app.research import ResearchLimits, research_location
from app.services.intelligence import IntelligenceService

DATA = Path(__file__).parent / "data"
CURRENT_RECORDING = DATA / "31g/sarvam-recording.json"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def load_corpus(path=DATA / "research_cases.json"):
    corpus = json.loads(path.read_text(encoding="utf-8"))
    ids = [case["id"] for case in corpus["cases"]]
    if len(ids) != len(set(ids)) or not all(re.fullmatch(r"[a-z0-9-]+", key) for key in ids):
        raise ValueError("invalid_case_ids")
    return corpus


class RecordedClient:
    """No network in replay. Missing/mismatched requests fail rather than invent responses."""

    def __init__(self, records, *, live=None, max_calls=40):
        self.records, self.live, self.max_calls = records, live, max_calls
        self.calls, self.positions = 0, Counter()
        self.elapsed = 0.0
        self.incomplete = 0
        self.failure = None

    async def complete(self, *, system, user, schema):
        request = {"system": system, "user": user, "schema": schema.model_json_schema()}
        key = fingerprint(request)
        if self.live is not None:
            if self.calls >= self.max_calls:
                self.failure = "benchmark_live_call_limit"
                raise RuntimeError("benchmark_live_call_limit")
            self.calls += 1
            started = time.monotonic()
            try:
                result = await self.live.complete(system=system, user=user, schema=schema)
            except asyncio.CancelledError:
                self.incomplete += 1
                self.records.setdefault(key, []).append(
                    {
                        "seconds": round(time.monotonic() - started, 4),
                        "interrupted": True,
                        "usage_complete": False,
                        "completion": Completion(error="deadline", requests=1).model_dump(
                            exclude={"request_id"}
                        ),
                    }
                )
                self.elapsed += time.monotonic() - started
                raise
            seconds = time.monotonic() - started
            usage_complete = not (result.error and result.requests and not result.usage)
            self.incomplete += int(not usage_complete)
            self.records.setdefault(key, []).append(
                {
                    "seconds": round(seconds, 4),
                    "completion": result.model_dump(exclude={"request_id"}),
                    "usage_complete": usage_complete,
                }
            )
        else:
            position = self.positions[key]
            rows = self.records.get(key, [])
            if position >= len(rows):
                self.failure = "benchmark_recording_miss"
                raise RuntimeError("benchmark_recording_miss")
            self.positions[key] += 1
            self.calls += 1
            seconds = rows[position]["seconds"]
            if rows[position].get("interrupted") or rows[position].get("usage_complete") is False:
                self.incomplete += 1
            payload = dict(rows[position]["completion"])
            if payload.get("data") is not None:
                payload["data"] = schema.model_validate(payload["data"])
            result = Completion(**payload)
        self.elapsed += seconds
        return result


class FixtureSources:
    """Controlled acquisition, not evidence of real search ranking or website availability."""

    def __init__(self, case):
        self.case = case
        self.objects = {}
        self.searches = self.fetches = 0

    async def search(self, query):
        self.searches += 1
        return SearchResponse(
            results=[
                SearchResult(url=f"https://example.org/{self.case['id']}", position=1, query=query)
            ],
            usage=SearchUsage(returned_results=1, reported_credits=0),
        )

    async def fetch(self, url):
        self.fetches += 1
        if self.case["fetch_error"]:
            raise FetchError(self.case["fetch_error"])
        return FetchedPage(url, self.case["text"].encode(), "text/plain")

    def put_object(self, *, Key, Body, **kwargs):
        self.objects.setdefault(Key, Body)

    def get_object(self, *, Key, **kwargs):
        return {"Body": io.BytesIO(self.objects[Key]), "ContentType": "text/plain"}


@asynccontextmanager
async def isolated_database(database_url):
    url = make_url(database_url)
    if url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("benchmark_requires_local_database")
    schema = "benchmark_" + uuid4().hex
    admin = create_async_engine(database_url, hide_parameters=True)
    engine = None
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(
            database_url,
            hide_parameters=True,
            connect_args={"options": f"-csearch_path={schema},public"},
        )
        async with engine.begin() as connection:
            await connection.run_sync(lambda conn: Base.metadata.create_all(conn, checkfirst=False))
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        if engine is not None:
            await engine.dispose()
        # Generated identifier is never supplied by a caller or derived from a case name.
        assert re.fullmatch(r"benchmark_[0-9a-f]{32}", schema)
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


async def seed(sessions, case):
    async with sessions() as session, session.begin():
        country = Location(
            name=case["country"],
            canonical_name=normalize_name(case["country"]),
            type="country",
            country_code=case["country_code"],
        )
        session.add(country)
        await session.flush()
        city = Location(
            name=case["city"],
            canonical_name=normalize_name(case["city"]),
            type="city",
            parent_id=country.id,
            country_code=case["country_code"],
        )
        session.add(city)
        await session.flush()
        return city.id


async def run_case(case, client, database_url):
    sources = FixtureSources(case)
    # No model downloads or synthetic vectors. New/exact deterministic hypotheses use
    # real production policy; semantic matching and embedding quality belong to PR 36.
    embedder = Embedder()
    rows = []
    async with isolated_database(database_url) as sessions:
        identity = await seed(sessions, case)

        async def on_claim(session, claim_id, budget):
            return await apply_and_score_claim(session, claim_id, embedder=embedder, client=budget)

        for temperature in ("cold", "warm"):
            before_calls, before_seconds, before_incomplete = (
                client.calls,
                client.elapsed,
                client.incomplete,
            )
            async with sessions() as session:
                result = await research_location(
                    session,
                    f"{case['city']}, {case['country']}",
                    target_location_id=identity,
                    provider=sources,
                    fetcher=sources,
                    storage=sources,
                    bucket="benchmark",
                    llm=client,
                    limits=ResearchLimits(
                        queries=1, candidates=2, fetches=1, documents=1, llm_calls=6
                    ),
                    on_claim=on_claim,
                )
                if client.failure:
                    raise RuntimeError(client.failure)
                await session.commit()
                claims = list(await session.scalars(select(Claim)))
                stored_documents = list(await session.scalars(select(Document)))
                empty_extractions = sum(
                    run.claim_count == 0 for run in await session.scalars(select(ClaimExtraction))
                )
                relevant = sum(
                    bool(a.get("completion", {}).get("data", {}).get("relevant"))
                    for doc in stored_documents
                    for a in doc.document_metadata.get("relevance", {}).get("assessments", [])
                    if a.get("completion", {}).get("data") is not None
                )
                paths, lineage = [], True
                documents_by_id = {doc.id: doc for doc in stored_documents}
                for claim in claims:
                    paths.append(
                        [node.name for node in await get_location_path(session, claim.location_id)]
                        if claim.location_id
                        else None
                    )
                    quote = claim.structured_data["evidence_quote"]
                    doc = documents_by_id[claim.document_id]
                    extraction = claim.structured_data["extraction"]
                    raw = sources.objects[doc.raw_object_path]
                    lineage &= (
                        quote in case["text"]
                        and hashlib.sha256(raw).hexdigest() == doc.content_hash
                        and doc.clean_text[extraction["quote_start"] : extraction["quote_end"]]
                        == quote
                        and await session.get(Source, doc.source_id) is not None
                    )
                supported = (
                    any(
                        all(
                            term in claim.structured_data["evidence_quote"]
                            for term in case["expected"]["evidence_terms"]
                        )
                        for claim in claims
                    )
                    if case["expected"]["claim"]
                    else not claims
                )
            public = await IntelligenceService(sessions).search_location_scams(identity)
            expected = case["expected"]
            timings = Counter()
            for item in result.diagnostics["stage_timings"]:
                timings[item["stage"]] += item["seconds"]
            rows.append(
                {
                    "case": case["id"],
                    "split": case["split"],
                    "cache": temperature,
                    "expected": expected,
                    "candidates": result.candidates,
                    "fetch_attempts": result.fetched,
                    "documents": len(result.documents),
                    "relevance_passages": sum(
                        d.get("relevance_passages", 0) for d in result.documents
                    ),
                    "relevant_passages": relevant,
                    "stored_empty_extractions": empty_extractions,
                    "claims": len(claims),
                    "resolved_claims": sum(p is not None for p in paths),
                    "public_patterns": public.total,
                    "complete": result.complete,
                    "result_status": result.status,
                    "incomplete_control_check": not result.complete
                    if case["fetch_error"] or not case["text"]
                    else None,
                    "grounded_claim_check": supported,
                    "lineage_check": bool(lineage),
                    "geography_check": (
                        bool(paths) and all(p == expected["location_path"] for p in paths)
                    )
                    if expected["claim"]
                    else not paths,
                    "public_check": (public.total > 0) == expected["public_pattern"],
                    "errors": dict(Counter(e["code"] for e in result.errors)),
                    "extraction_errors": dict(
                        Counter(
                            a["error"]
                            for d in result.documents
                            for a in d.get("extraction_attempts", [])
                            if a.get("error")
                        )
                    ),
                    "llm_calls": client.calls - before_calls,
                    "provider_seconds": round(client.elapsed - before_seconds, 4),
                    "usage_complete": client.incomplete == before_incomplete,
                    "provider_timing_complete": client.incomplete == before_incomplete,
                    "stage_seconds": {k: round(v, 3) for k, v in timings.items()},
                    "tokens": sum(
                        u.get("usage", {}).get("total_tokens", 0) for u in result.usage["llm"]
                    ),
                }
            )
    return rows


def summarize(rows):
    return {
        cache: {
            "cases": len(selected := [r for r in rows if r["cache"] == cache]),
            "grounded_claim_checks_passed": sum(r["grounded_claim_check"] for r in selected),
            "geography_checks_passed": sum(r["geography_check"] for r in selected),
            "public_checks_passed": sum(r["public_check"] for r in selected),
            "llm_calls": sum(r["llm_calls"] for r in selected),
            "tokens": sum(r["tokens"] for r in selected),
            "usage_complete": all(r.get("usage_complete", True) for r in selected),
        }
        for cache in ("cold", "warm")
    }


def scorecard(rows):
    """Targets fixed before the baseline; zero-denominator metrics are not a pass."""
    result = {}
    for cache in ("cold", "warm"):
        selected = [r for r in rows if r["cache"] == cache]
        positives = [r for r in selected if r["expected"]["claim"]]
        scoped = [r for r in positives if r["expected"]["location_path"]]
        eligible = [r for r in selected if r["expected"]["public_pattern"]]
        negatives = [r for r in selected if not r["expected"]["claim"]]
        withheld = [r for r in selected if not r["expected"]["public_pattern"]]
        metrics = {
            "positive_claim_coverage": (
                sum(r["grounded_claim_check"] for r in positives),
                len(positives),
                0.8,
            ),
            "correct_geography_coverage": (
                sum(r["geography_check"] for r in scoped),
                len(scoped),
                0.9,
            ),
            "eligible_pattern_coverage": (
                sum(r["public_check"] for r in eligible),
                len(eligible),
                0.8,
            ),
            "negative_claim_safety": (
                sum(r["claims"] == 0 for r in negatives),
                len(negatives),
                1.0,
            ),
            "withheld_pattern_safety": (
                sum(r["public_patterns"] == 0 for r in withheld),
                len(withheld),
                1.0,
            ),
        }
        result[cache] = {
            name: {
                "passed": n,
                "total": d,
                "target": target,
                "meets_target": bool(d) and n / d >= target,
            }
            for name, (n, d, target) in metrics.items()
        }
    return result


async def run(args):
    corpus = load_corpus()
    if args.mode == "live" and args.recording.exists():
        raise ValueError("benchmark_recording_already_exists_choose_new_path")
    cassette = (
        json.loads(args.recording.read_text())
        if args.mode == "replay"
        else {"corpus_hash": fingerprint(corpus), "model": MODEL, "records": {}}
    )
    if cassette["corpus_hash"] != fingerprint(corpus):
        raise ValueError("benchmark_corpus_changed")
    if cassette.get("model") != MODEL:
        raise ValueError("benchmark_model_changed")
    settings = Settings()
    client = RecordedClient(
        cassette["records"],
        max_calls=args.max_live_calls,
        live=Sarvam(settings.sarvam_api_key) if args.mode == "live" else None,
    )
    rows = []
    try:
        for case in corpus["cases"]:
            rows.extend(await run_case(case, client, settings.database_url.get_secret_value()))
            print(f"Completed {case['id']}", flush=True)
    finally:
        if args.mode == "live":
            args.recording.parent.mkdir(parents=True, exist_ok=True)
            args.recording.write_text(json.dumps(cassette, indent=2) + "\n")
    report = {
        "version": "research-benchmark-v1",
        "mode": args.mode,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "created_at": datetime.now(UTC).isoformat(),
        "corpus_hash": fingerprint(corpus),
        "versions": {
            "model": MODEL,
            "relevance": RELEVANCE_VERSION,
            "extraction": EXTRACTION_VERSION,
            "processing": PROCESSING_VERSION,
            "location": RESOLUTION_VERSION,
            "discovery": DISCOVERY_VERSION,
            "acquisition": POLICY_VERSION,
        },
        "limitations": [
            "Synthetic sources; search and acquisition are controlled.",
            "Replay timings are local execution, provider_seconds are historical recordings.",
            "Warm means reprocessing identical source bytes, not the fresh-location fast read.",
            "No semantic matching, live fetch or embedding performance conclusions.",
            "Evidence-term checks do not replace human review of claim meaning.",
        ],
        "summary": summarize(rows),
        "scorecard": scorecard(rows),
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["replay", "live"], default="replay")
    parser.add_argument("--recording", type=Path, default=CURRENT_RECORDING)
    parser.add_argument("--output", type=Path, default=Path(".tools/research-benchmark.json"))
    parser.add_argument("--max-live-calls", type=int, choices=range(1, 101), default=40)
    args = parser.parse_args()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(run(args))


if __name__ == "__main__":
    main()
