"""Explicit local sizing experiment. Requires precreated capped cgroup and Docker.

No production secrets or paid resources are used. Raw metrics go to ignored .tools.
"""

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / ".tools" / "droplet-sizing-20261103-run3"
PROJECT = "travel-sizing-20261103"
ENV = {
    **os.environ,
    "APP_IMAGE": "travel-scam-sizing:20261103",
    "S3_ENDPOINT": "http://object-store:9000",
    "S3_BUCKET": "sizing-only",
    "MODEL_CACHE": str(ROOT / ".tools/embedding-cache"),
    "SECRETS_PATH": str(OUTPUT / "secrets"),
    "API_BIND_PORT": "18013",
    "BENCH_ROOT": str(ROOT),
}
COMPOSE = [
    "docker",
    "compose",
    "-p",
    PROJECT,
    "--env-file",
    "deploy/production.env.example",
    "-f",
    "deploy/compose.yaml",
    "-f",
    "benchmarks/droplet_sizing/compose.yaml",
]
RESULTS = []


def command(args, *, timeout=240, check=True):
    result = subprocess.run(
        args, env=ENV, cwd=ROOT, capture_output=True, text=True, timeout=timeout
    )
    if check and result.returncode:
        raise RuntimeError(
            f"Command failed: {args}; {result.stderr[-2000:]} {result.stdout[-2000:]}"
        )
    return result.stdout


def compose(*args, **kwargs):
    return command([*COMPOSE, *args], **kwargs)


def phase(name):
    print(name, flush=True)
    with (OUTPUT / "phases.jsonl").open("a") as stream:
        stream.write(json.dumps({"phase": name, "time": time.time()}) + "\n")


def record(value):
    RESULTS.append(value)
    with (OUTPUT / "results.jsonl").open("a") as stream:
        stream.write(json.dumps(value) + "\n")


def location(index):
    return str(uuid5(NAMESPACE_URL, f"sizing-only:location:{index}"))


def sql(query):
    return compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "travel",
        "-d",
        "travel_scam_intel",
        "-tA",
        "-c",
        query,
    ).strip()


async def requests(name, count, concurrency=1, *, match=False, read_types=None):
    phase(name)
    durations, statuses, semantic, limitations = [], {}, {}, {}
    started = time.perf_counter()
    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(base_url="http://127.0.0.1:18012", timeout=45) as client:

        async def one(index):
            async with semaphore:
                before = time.perf_counter()
                try:
                    if match:
                        response = await client.post(
                            "/v1/hypotheses/match",
                            json={
                                "location_id": location(0),
                                "incident": (
                                    "A driver demanded extra money after I had agreed "
                                    "to a fixed fare."
                                ),
                                "min_similarity": 0.5,
                            },
                        )
                    else:
                        choices = read_types or ["hypotheses", "summary", "recent"]
                        suffix = choices[index % len(choices)]
                        response = await client.get(
                            f"/v1/locations/{location(index % 50)}/{suffix}?research=false"
                        )
                    key = str(response.status_code)
                    data = response.json()
                    outcome = data.get("status") or data.get("outcome") or "response"
                    semantic[outcome] = semantic.get(outcome, 0) + 1
                    for limitation in data.get("limitations", []):
                        limitations[limitation] = limitations.get(limitation, 0) + 1
                    if not match and suffix != "summary" and not data.get("items"):
                        semantic["unexpected_empty"] = semantic.get("unexpected_empty", 0) + 1
                except Exception as exc:
                    key = type(exc).__name__
                statuses[key] = statuses.get(key, 0) + 1
                durations.append(time.perf_counter() - before)

        await asyncio.gather(*(one(i) for i in range(count)))
    elapsed = time.perf_counter() - started
    ordered = sorted(durations)
    record = {
        "phase": name,
        "requests": count,
        "concurrency": concurrency,
        "elapsed_seconds": elapsed,
        "requests_per_second": count / elapsed,
        "p50_ms": ordered[len(ordered) // 2] * 1000,
        "p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] * 1000,
        "max_ms": max(ordered) * 1000,
        "statuses": statuses,
        "outcomes": semantic,
        "limitations": limitations,
    }
    RESULTS.append(record)
    with (OUTPUT / "results.jsonl").open("a") as stream:
        stream.write(json.dumps(record) + "\n")
    print(json.dumps(record), flush=True)


async def reports():
    phase("submit_5_reports_and_poll")
    receipts = []
    async with httpx.AsyncClient(base_url="http://127.0.0.1:18012", timeout=45) as client:
        for index in range(5):
            started = time.perf_counter()
            body = (
                "I agreed to a fixed taxi fare before the journey, but the driver later "
                "demanded extra money. This is a synthetic server sizing report. "
            )
            response = await client.post(
                "/v1/reports",
                json={
                    "report_text": (body * 35) + f" Observation number {index}.",
                    "location_id": location(50),
                    "provenance_class": "FIRST_HAND",
                },
                headers={"Idempotency-Key": f"sizing-{uuid4()}"},
            )
            response.raise_for_status()
            receipts.append(
                {
                    "id": response.json()["report_id"],
                    "submitted": time.time(),
                    "accept_ms": (time.perf_counter() - started) * 1000,
                }
            )
            await asyncio.sleep(1)
        for _ in range(90):
            states = await asyncio.to_thread(
                sql,
                "SELECT json_agg(json_build_object('id',id,'status',processing_status,"
                "'details',processing_details)) FROM user_reports",
            )
            parsed = json.loads(states)
            if all(row["status"] in ("processed", "needs_review", "failed") for row in parsed):
                break
            await asyncio.sleep(1)
        record(
            {
                "phase": "report_outcomes",
                "receipts": receipts,
                "states": parsed,
                "finished": time.time(),
            }
        )
        print(
            json.dumps(
                {
                    "report_states": [
                        {
                            "id": row["id"],
                            "status": row["status"],
                            "errors": row["details"].get("error_codes"),
                        }
                        for row in parsed
                    ]
                }
            ),
            flush=True,
        )


async def workloads():
    phase("idle_unloaded_20s")
    await asyncio.sleep(20)
    phase("seed_1000_patterns_5000_claims")
    print(
        await asyncio.to_thread(
            compose, "exec", "-T", "api", "python", "-m", "benchmarks.droplet_sizing.seed"
        ),
        flush=True,
    )
    await requests("cached_reads_100_serial", 100)
    await requests("cached_reads_100_concurrency5", 100, 5)
    await requests("cold_embedding_match", 1, match=True)
    await requests("warm_embedding_match_20_serial", 20, match=True)
    phase("mixed_reads_and_reports")
    await asyncio.gather(requests("mixed_reads_100_concurrency2", 100, 2), reports())
    phase("idle_both_models_loaded_20s")
    await asyncio.sleep(20)
    await requests("mixed_match_burst_20_concurrency3", 20, 3, match=True)
    phase("on_demand_research_with_cached_reads")
    async with httpx.AsyncClient(base_url="http://127.0.0.1:18012", timeout=45) as client:
        response = await client.get(f"/v1/locations/{location(51)}/summary")
        response.raise_for_status()
    await requests("reads_during_research_100_concurrency2", 100, 2)
    for _ in range(60):
        research = await asyncio.to_thread(
            sql,
            "SELECT json_agg(json_build_object('status',status,'progress',progress,"
            "'error',error_code)) FROM location_research_states",
        )
        if research and research != "null":
            states = json.loads(research)
            if all(row["status"] != "researching" for row in states):
                break
        await asyncio.sleep(1)
    record({"phase": "research_outcomes", "states": json.loads(research)})
    phase("grow_10000_patterns_50000_claims")
    print(
        await asyncio.to_thread(
            compose,
            "exec",
            "-T",
            "api",
            "python",
            "-m",
            "benchmarks.droplet_sizing.seed",
            "--start",
            "1000",
            "--stop",
            "10000",
        ),
        flush=True,
    )
    # Grown-corpus probes are bounded separately; a client timeout does not cancel
    # PostgreSQL work. See probe.py for explicit isolated-backend cancellation.
    phase("done")


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "secrets").mkdir(exist_ok=True)
    if (OUTPUT / "phases.jsonl").exists():
        raise SystemExit("Use a fresh output directory for a new run.")
    for key, value in {
        "DATABASE_URL": "postgresql+psycopg://travel:sizing-only@postgres:5432/travel_scam_intel",
        "POSTGRES_PASSWORD": "sizing-only",
        "REDIS_URL": "redis://redis:6379/0",
        "S3_ACCESS_KEY": "travel-local",
        "S3_SECRET_KEY": "travel-local-password",
        "SERPER_API_KEY": "",
        "SARVAM_API_KEY": "",
        "ADMIN_API_TOKEN": "sizing-only",
    }.items():
        (OUTPUT / "secrets" / key).write_text(value)
    metrics_file = (OUTPUT / "metrics.jsonl").open("w")
    monitor = subprocess.Popen(
        [
            "docker",
            "run",
            "--rm",
            "--name",
            f"{PROJECT}-monitor",
            "--privileged",
            "--cgroupns=host",
            "--mount",
            "type=bind,source=/sys/fs/cgroup,target=/hostcg,readonly",
            "--mount",
            f"type=bind,source={ROOT},target=/bench,readonly",
            "--entrypoint",
            "python",
            ENV["APP_IMAGE"],
            "-u",
            "/bench/benchmarks/droplet_sizing/monitor.py",
        ],
        stdout=metrics_file,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        phase("start_dependencies_and_migrate")
        compose("up", "-d", "--wait", "postgres", "redis", "object-store")
        compose("run", "--rm", "--no-deps", "api", "alembic", "upgrade", "head")
        compose("run", "--rm", "--no-deps", "api", "python", "-m", "app.integrations.storage")
        phase("start_api_worker_proxy")
        compose("up", "-d", "--wait", "--wait-timeout", "180", "api", "worker", "proxy")
        asyncio.run(workloads())
    finally:
        (OUTPUT / "results.json").write_text(json.dumps(RESULTS, indent=2))
        ids = compose("ps", "-q").split()
        if ids:
            (OUTPUT / "containers.json").write_text(command(["docker", "inspect", *ids]))
        command(["docker", "stop", f"{PROJECT}-monitor"], check=False)
        monitor.wait(timeout=20)
        metrics_file.close()
        (OUTPUT / "logs.txt").write_text(compose("logs", "--no-color", "--tail", "250"))


if __name__ == "__main__":
    main()
