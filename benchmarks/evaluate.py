"""Run PR36 labeled evaluation; replay is network-free and local-database-only."""

import argparse
import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.confidence import VERSION as CONFIDENCE_VERSION
from app.core.config import Settings
from app.db.hypothesis_lifecycle import Review, apply_resolution
from app.db.hypothesis_resolution import resolve_hypothesis
from app.db.resolution import RESOLUTION_VERSION, resolve_location
from app.duplicates import VERSION as DUPLICATE_VERSION
from app.embeddings import text_hash
from app.extraction import PROMPT_VERSION as EXTRACTION_VERSION
from app.extraction import extract_passage
from app.hypothesis_resolution import VERSION as MATCHING_VERSION
from app.independence import VERSION as INDEPENDENCE_VERSION
from app.integrations.sarvam import MODEL, Sarvam
from app.models import Claim, Document, Evidence, Hypothesis, Location, Source
from app.models.location import LocationType
from app.relevance import Passage
from benchmarks.evaluation_metrics import (
    DATA,
    compare_baseline,
    extraction_checks,
    fingerprint,
    load_corpus,
    scorecard,
)
from benchmarks.evaluation_policies import NOW, deterministic
from benchmarks.research import RecordedClient, isolated_database

SEMANTIC_FILES = [
    "app/extraction.py",
    "app/relevance.py",
    "app/hypothesis_resolution.py",
    "app/db/hypothesis_resolution.py",
    "app/db/resolution.py",
    "app/db/embeddings.py",
    "app/db/hypothesis_lifecycle.py",
    "app/integrations/sarvam.py",
]
POLICY_FILES = [
    "app/duplicates.py",
    "app/db/duplicates.py",
    "app/independence.py",
    "app/confidence.py",
    "app/evidence_weight.py",
    "app/scheduling.py",
    "app/db/scheduler.py",
]
HARNESS_FILES = [
    "benchmarks/evaluate.py",
    "benchmarks/evaluation_metrics.py",
    "benchmarks/evaluation_policies.py",
    "benchmarks/research.py",
]
ROOT = Path(__file__).resolve().parents[1]


def sources(paths):
    return {path: text_hash((ROOT / path).read_text(encoding="utf-8")) for path in paths}


def identity(value):
    return uuid5(NAMESPACE_URL, "tsi-evaluation-v1:" + value)


class ControlledEmbedder:
    """All in-scope candidates are retrieved; this is explicitly NOT an embedding-quality test."""

    model = "evaluation-controlled-vectors"
    revision = "v1"

    async def embed(self, texts):
        return [[1.0] + [0.0] * 383 for _ in texts]


async def seed_locations(sessions, corpus):
    async with sessions() as session, session.begin():
        for row in corpus["locations"]:
            session.add(
                Location(
                    id=identity("location:" + row["id"]),
                    name=row["name"],
                    canonical_name=row["name"].casefold(),
                    type=LocationType(row["type"]),
                    country_code=row["country_code"],
                    parent_id=identity("location:" + row["parent"]) if row["parent"] else None,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            await session.flush()


async def matching(sessions, case, client):
    inputs = case["input"]
    embedder = ControlledEmbedder()
    candidate = inputs["candidate"]
    async with sessions() as session:
        transaction = await session.begin()
        try:
            source = Source(
                id=identity("source:" + case["id"]),
                platform="synthetic-evaluation",
                source_type="blog",
            )
            session.add(source)
            await session.flush()
            document = Document(
                id=identity("document:" + case["id"]),
                source_id=source.id,
                raw_object_path="synthetic/no-object",
                content_hash=text_hash(case["id"]),
                created_at=NOW,
                updated_at=NOW,
            )
            session.add(document)
            await session.flush()
            title = "Synthetic " + candidate["id"]
            description = candidate["description"]
            target = Hypothesis(
                id=identity("hypothesis:" + case["id"]),
                primary_location_id=identity("location:" + candidate["location"]),
                title=title,
                canonical_description=description,
                category="transport",
                embedding=[1.0] + [0.0] * 383,
                embedding_model=embedder.model,
                embedding_revision=embedder.revision,
                embedding_input_hash=text_hash(title + "\n" + description),
                created_at=NOW,
                updated_at=NOW,
            )
            claim = Claim(
                id=identity("claim:" + case["id"]),
                document_id=document.id,
                location_id=identity("location:" + inputs["location"]),
                claim_text=inputs["claim"],
                normalized_claim=inputs["claim"].casefold(),
                claim_type="transport",
                structured_data={
                    "publication_status": "unreviewed",
                    "sensitive": False,
                    "entities": [],
                    "evidence_quote": inputs["claim"],
                },
                created_at=NOW,
            )
            session.add_all([target, claim])
            await session.flush()
            resolution = await resolve_hypothesis(
                session, claim.id, embedder=embedder, client=client
            )
            observed = {
                "decision": resolution.decision,
                "target": candidate["id"] if resolution.target_id == target.id else None,
                "stance": None,
                "review_required": resolution.needs_review,
                "candidate_count": len(resolution.details.get("context", {}).get("candidates", [])),
            }
            if resolution.error:
                return observed, resolution.error
            event = await apply_resolution(
                session,
                resolution.id,
                embedder=embedder,
                review=Review(
                    actor="evaluation", reason="Isolated labeled evaluation; no publication."
                ),
            )
            evidence = await session.get(Evidence, event.details["evidence_id"])
            observed["stance"] = evidence.stance.value
            return observed, None
        finally:
            await transaction.rollback()


async def geography(sessions, case):
    inputs = case["input"]
    async with sessions() as session:
        transaction = await session.begin()
        try:
            result = await resolve_location(
                session,
                inputs["text"],
                parent_id=identity("location:" + inputs["parent"]) if inputs["parent"] else None,
            )
            return {"status": result.status, "path": [node.name for node in result.hierarchy]}
        finally:
            await transaction.rollback()


async def evaluate(corpus, client, database_url, progress=None):
    rows = []
    async with isolated_database(database_url) as sessions:
        await seed_locations(sessions, corpus)
        for case in corpus["cases"]:
            row = {key: case[key] for key in ["id", "task", "split"]}
            row.update(input_hash=fingerprint(case["input"]), error=None, observed={}, checks={})
            task = case["task"]
            try:
                if task == "extraction":
                    text = case["input"]["text"]
                    completion = await extract_passage(
                        Passage(index=0, start=0, end=len(text), text=text), client
                    )
                    claims = (
                        [c.model_dump() for c in completion.data.claims]
                        if completion.data is not None
                        else []
                    )
                    error = completion.error or (
                        "missing_completion" if completion.data is None else None
                    )
                    counts, checks = extraction_checks(case, claims, error)
                    row.update(
                        error=error, observed={"claims": claims, "counts": counts}, checks=checks
                    )
                elif task == "location":
                    observed = await geography(sessions, case)
                    row.update(
                        observed=observed,
                        checks={
                            "status": observed["status"] == case["expected"]["status"],
                            "path": observed["path"] == case["expected"]["path"],
                        },
                    )
                elif task == "matching":
                    observed, error = await matching(sessions, case, client)
                    row.update(
                        observed=observed,
                        error=error,
                        checks={
                            key: not error and observed.get(key) == case["expected"][key]
                            for key in ["decision", "target", "stance"]
                        },
                    )
                else:
                    observed, checks = deterministic(case)
                    row.update(observed=observed, checks=checks)
            except RuntimeError as exc:
                # A replay miss/call cap is a harness execution failure, never a quality pass.
                if "recording_miss" in str(exc) or "live_call_limit" in str(exc):
                    raise
                raise RuntimeError("evaluation_execution_error:" + case["id"]) from exc
            rows.append(row)
            if progress:
                progress(row)
    return rows


def assemble(corpus, rows, *, recording_hash, mode, capture_time, client):
    card = scorecard(corpus, rows)
    return {
        "version": "evaluation-report-v1",
        "corpus_hash": fingerprint(corpus),
        "mode": mode,
        "recording_hash": recording_hash,
        "recorded_at": capture_time,
        "source_hashes": sources(SEMANTIC_FILES + POLICY_FILES),
        "harness_hashes": sources(HARNESS_FILES),
        "versions": {
            "model": MODEL,
            "extraction": EXTRACTION_VERSION,
            "location": RESOLUTION_VERSION,
            "matching": MATCHING_VERSION,
            "duplicates": DUPLICATE_VERSION,
            "independence": INDEPENDENCE_VERSION,
            "confidence": CONFIDENCE_VERSION,
            "vectors": "controlled-v1 (not a trained retrieval measurement)",
        },
        "source_tree_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
        ),
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "quality_passed": all(
            metric["meets_target"]
            for split in card.values()
            for group in ["tasks", "metrics"]
            for metric in split[group].values()
        ),
        "execution_passed": not any(row["error"] for row in rows),
        "scorecard": card,
        "cases": rows,
        "provider": {
            "recorded_calls_consumed": client.calls,
            "live_calls_this_run": client.calls if mode == "capture" else 0,
            "recorded_tokens": 0,
            "usage_complete": client.incomplete == 0,
        },
        "limitations": [
            "Original small synthetic regression corpus, not population accuracy or scam truth.",
            "Mechanism-term and quote matching is a bounded extraction proxy, "
            "not semantic equivalence.",
            "Controlled vectors expose all in-scope candidates; trained retrieval quality "
            "and latency are not measured.",
            "Stance is the stored attachment stance after reviewed lifecycle application; "
            "RELATED/SUBTYPE create supporting evidence on their own child pattern.",
            "Synthetic sources only; no Serper, live acquisition, R2 or production writes.",
            "Replay model responses/timings are historical; prompt/source changes "
            "require explicit recapture.",
        ],
    }


async def run(args):
    if args.output.resolve() in {args.corpus.resolve(), args.recording.resolve()}:
        raise ValueError("output_would_overwrite_input")
    corpus = load_corpus(args.corpus)
    source_signature = fingerprint(sources(SEMANTIC_FILES))
    if args.mode == "capture":
        if args.recording.exists() and not args.overwrite:
            raise ValueError("capture_refuses_to_overwrite_recording")
        records = {}
        capture_time = datetime.now(UTC).isoformat()
        client = RecordedClient(
            records,
            live=Sarvam(Settings().sarvam_api_key, timeout=25, concurrency=1),
            max_calls=args.max_calls,
        )
    else:
        capture = json.loads(args.recording.read_text(encoding="utf-8"))
        if (
            capture["version"] != "evaluation-recording-v1"
            or capture["corpus_hash"] != fingerprint(corpus)
            or capture["semantic_source_hash"] != source_signature
        ):
            raise ValueError("recording_inputs_or_semantic_sources_changed")
        records = capture["records"]
        capture_time = capture["recorded_at"]
        client = RecordedClient(records)

    def persist_capture():
        if args.mode != "capture":
            return
        args.recording.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.recording.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "version": "evaluation-recording-v1",
                    "corpus_hash": fingerprint(corpus),
                    "semantic_source_hash": source_signature,
                    "recorded_at": capture_time,
                    "records": records,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(args.recording)

    def progress(row):
        print(
            json.dumps(
                {
                    "case": row["id"],
                    "task": row["task"],
                    "error": row["error"],
                    "checks": row["checks"],
                }
            ),
            flush=True,
        )
        persist_capture()

    try:
        rows = await evaluate(corpus, client, args.database_url, progress)
    finally:
        persist_capture()
    if args.mode == "replay" and dict(client.positions) != {
        key: len(sequence) for key, sequence in records.items()
    }:
        raise ValueError("recording_unused_or_duplicate_entries")
    report = assemble(
        corpus,
        rows,
        recording_hash=fingerprint(records),
        mode=args.mode,
        capture_time=capture_time,
        client=client,
    )
    completions = [value["completion"] for sequence in records.values() for value in sequence]
    report["provider"]["recorded_tokens"] = sum(
        value.get("usage", {}).get("total_tokens", 0) for value in completions
    )
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        if baseline["scorecard"] != scorecard(corpus, baseline["cases"]):
            raise ValueError("baseline_scorecard_mismatch")
        report["baseline_comparison"] = compare_baseline(report, baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "quality_passed": report["quality_passed"],
                "execution_passed": report["execution_passed"],
                "baseline_comparison": report.get("baseline_comparison"),
            }
        )
    )
    if not report["execution_passed"]:
        return 2
    return (
        0
        if report["quality_passed"]
        and (report["baseline_comparison"]["passed"] if args.baseline else True)
        else 1
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["replay", "capture"], default="replay")
    parser.add_argument("--corpus", type=Path, default=DATA / "corpus.json")
    parser.add_argument("--recording", type=Path, default=DATA / "sarvam-recording.json")
    parser.add_argument("--output", type=Path, default=Path(".tools/evaluation/report.json"))
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--max-calls", type=int, choices=range(1, 41), default=20)
    parser.add_argument(
        "--database-url",
        default=Settings(_env_file=None, _secrets_dir=None).database_url.get_secret_value(),
    )
    args = parser.parse_args()
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            result = runner.run(run(args))
    except Exception as exc:
        # Provider/DB/validation exception strings can contain private input or credentials.
        failure = {
            "version": "evaluation-report-v1",
            "execution_passed": False,
            "quality_passed": False,
            "execution_error": type(exc).__name__,
        }
        try:
            if args.output.resolve() not in {args.corpus.resolve(), args.recording.resolve()}:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(failure, indent=2) + "\n", encoding="utf-8")
        except OSError:
            # Stdout remains the failure artifact when the requested destination is unwritable.
            failure["report_write_failed"] = True
        print(json.dumps(failure))
        result = 2
    raise SystemExit(result)


if __name__ == "__main__":
    main()
