"""Export compact, credential-free measurement evidence from the local traces."""

import bisect
import csv
import gzip
import json
from collections import defaultdict

from benchmarks.droplet_sizing import run

DESTINATION = run.ROOT / "benchmarks/results/2026-11-03"


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    phases = sorted(
        (json.loads(line) for line in (run.OUTPUT / "phases.jsonl").read_text().splitlines()),
        key=lambda row: row["time"],
    )
    starts = [row["time"] for row in phases]
    groups = defaultdict(list)
    fields = [
        "source",
        "time",
        "phase",
        "memory_bytes",
        "anon_bytes",
        "file_bytes",
        "kernel_bytes",
        "shmem_bytes",
        "swap_bytes",
        "cpu_usage_usec",
        "cpu_throttled_usec",
        "oom_kills",
    ]
    with gzip.open(DESTINATION / "memory-cpu-trace.csv.gz", "wt", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for source in sorted(run.OUTPUT.glob("metrics*.jsonl")):
            for line in source.read_text().splitlines():
                row = json.loads(line)
                index = max(0, bisect.bisect_right(starts, row["time"]) - 1)
                phase = phases[index]["phase"]
                groups[(source.name, phase)].append(row)
                writer.writerow(
                    {
                        "source": source.name,
                        "time": row["time"],
                        "phase": phase,
                        "memory_bytes": row["memory"],
                        "anon_bytes": row["stat"]["anon"],
                        "file_bytes": row["stat"]["file"],
                        "kernel_bytes": row["stat"]["kernel"],
                        "shmem_bytes": row["stat"]["shmem"],
                        "swap_bytes": row["swap"],
                        "cpu_usage_usec": row["cpu"]["usage_usec"],
                        "cpu_throttled_usec": row["cpu"]["throttled_usec"],
                        "oom_kills": row["events"]["oom_kill"],
                    }
                )
    summaries = []
    for (source, phase), rows in groups.items():
        summaries.append(
            {
                "source": source,
                "phase": phase,
                "samples": len(rows),
                "first_time": rows[0]["time"],
                "last_time": rows[-1]["time"],
                "memory_min_MiB": round(min(row["memory"] for row in rows) / 2**20, 2),
                "memory_max_MiB": round(max(row["memory"] for row in rows) / 2**20, 2),
                "anon_kernel_shmem_max_MiB": round(
                    max(
                        row["stat"]["anon"] + row["stat"]["kernel"] + row["stat"]["shmem"]
                        for row in rows
                    )
                    / 2**20,
                    2,
                ),
                "oom_kills_first": rows[0]["events"]["oom_kill"],
                "oom_kills_last": rows[-1]["events"]["oom_kill"],
                "swap_max_bytes": max(row["swap"] for row in rows),
            }
        )
    (DESTINATION / "resource-summary.json").write_text(json.dumps(summaries, indent=2))
    # Initial baseline summary was captured in the tool console before interrupting
    # the unbounded grown-corpus recent query. There is no fabricated per-request log.
    baseline = [
        {
            "phase": "cached_reads_100_serial",
            "requests": 100,
            "concurrency": 1,
            "p50_ms": 203.04,
            "p95_ms": 489.27,
            "http_200": 100,
            "findings": 100,
        },
        {
            "phase": "cached_reads_100_concurrency5",
            "requests": 100,
            "concurrency": 5,
            "p50_ms": 1177.18,
            "p95_ms": 1974.67,
            "http_200": 100,
            "findings": 100,
        },
        {
            "phase": "cold_embedding_match",
            "requests": 1,
            "elapsed_ms": 1328.08,
            "http_200": 1,
            "matched": 1,
        },
        {
            "phase": "warm_embedding_match_20_serial",
            "requests": 20,
            "concurrency": 1,
            "p50_ms": 401.94,
            "p95_ms": 527.96,
            "http_200": 20,
            "matched": 20,
        },
        {
            "phase": "mixed_reads_100_concurrency2",
            "requests": 100,
            "concurrency": 2,
            "p50_ms": 472.77,
            "p95_ms": 1476.20,
            "http_200": 100,
            "findings": 100,
        },
        {
            "phase": "mixed_match_burst_20_concurrency3",
            "requests": 20,
            "concurrency": 3,
            "http_200": 20,
            "matched": 5,
            "unavailable": 15,
        },
        {
            "phase": "reads_during_research_100_concurrency2",
            "requests": 100,
            "concurrency": 2,
            "p50_ms": 446.57,
            "p95_ms": 1093.11,
            "http_200": 100,
            "findings": 100,
        },
    ]
    evidence = {
        "date": "2026-11-03",
        "baseline_source": "Captured benchmark console summaries; memory trace retained",
        "limitations": [
            "Local Docker Desktop/WSL emulation, not a DigitalOcean VM",
            "External services simulated; HTTP proxy tested without TLS",
            "Initial large recent-query phase interrupted; no successful p95 claimed",
        ],
        "baseline": baseline,
        "probes": json.loads((run.OUTPUT / "results-probe.json").read_text()),
        "media": json.loads((run.OUTPUT / "results-media.json").read_text()),
        "memory_only_control": json.loads(
            (run.OUTPUT / "results-media-memory-only.json").read_text()
        ),
    }
    (DESTINATION / "request-results.json").write_text(json.dumps(evidence, indent=2))
    print(f"Exported {sum(len(rows) for rows in groups.values())} resource samples.")


if __name__ == "__main__":
    main()
