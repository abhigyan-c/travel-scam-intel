"""Bounded grown-corpus probes after the initial long-query failure."""

import asyncio
import json
import subprocess
import time

import httpx

from benchmarks.droplet_sizing import run
from benchmarks.droplet_sizing.control import controls


async def cancel_queries():
    await asyncio.to_thread(
        run.sql,
        "SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE state='active' "
        "AND pid<>pg_backend_pid() AND datname='travel_scam_intel'",
    )


async def reads(label):
    run.phase(label)
    async with httpx.AsyncClient(base_url="http://127.0.0.1:18012", timeout=10) as client:
        for suffix in ("hypotheses", "summary", "recent"):
            before = time.perf_counter()
            try:
                response = await client.get(
                    f"/v1/locations/{run.location(0)}/{suffix}?research=false"
                )
                status = str(response.status_code)
            except httpx.TimeoutException:
                status = "timeout_10s"
            row = {
                "phase": label,
                "endpoint": suffix,
                "status": status,
                "elapsed_seconds": time.perf_counter() - before,
            }
            run.record(row)
            print(json.dumps(row), flush=True)
            await cancel_queries()


async def workloads():
    await cancel_queries()
    await reads("grown_1cpu_2GiB_bounded")
    controls(3758096384, 200000, "0-1")
    await reads("grown_2cpu_4GiB_control_bounded")
    await run.requests("grown_4GiB_match_burst20_concurrency3", 20, 3, match=True)
    await cancel_queries()
    controls(1610612736, 100000, "0")
    await run.requests("grown_2GiB_match_burst20_concurrency3", 20, 3, match=True)
    controls(1610612736, 50000, "0")
    await run.requests(
        "half_cpu_grown_summary_and_search20_serial", 20, read_types=["hypotheses", "summary"]
    )
    await run.requests("half_cpu_grown_match10_serial", 10, match=True)
    controls(1610612736, 100000, "0")
    run.phase("backup_grown_corpus_2GiB")
    before = time.perf_counter()
    output = await asyncio.to_thread(
        run.command,
        [
            "docker",
            "run",
            "--rm",
            "--cgroup-parent",
            "travel-scam-sizing-20261103",
            "-v",
            "/var/run/docker.sock:/var/run/docker.sock",
            "--entrypoint",
            "bash",
            "travel-scam-sizing-backup:20261103",
            "-c",
            "set -euo pipefail; printf synthetic-sizing-only > /tmp/passphrase; "
            "docker exec travel-sizing-20261103-postgres-1 "
            "pg_dump -U travel -d travel_scam_intel -Fc | "
            "gpg --batch --pinentry-mode loopback --passphrase-file /tmp/passphrase "
            "--symmetric --cipher-algo AES256 --output /tmp/backup.dump.gpg; "
            "wc -c /tmp/backup.dump.gpg",
        ],
    )
    row = {
        "phase": "backup_grown_corpus_2GiB",
        "elapsed_seconds": time.perf_counter() - before,
        "encrypted_dump_size": output.strip(),
    }
    run.record(row)
    print(json.dumps(row), flush=True)
    run.phase("probe_final_idle_10s")
    await asyncio.sleep(10)
    print(
        await asyncio.to_thread(
            run.sql,
            "SELECT json_build_object('reports',(SELECT json_agg(json_build_object('status',"
            "processing_status,'details',processing_details)) FROM user_reports),'research',"
            "(SELECT json_agg(json_build_object('status',status,'progress',progress,'error',"
            "error_code)) FROM location_research_states),"
            "'size',pg_database_size(current_database()))",
        ),
        flush=True,
    )
    run.phase("probe_done")


def main():
    run.command(["docker", "stop", f"{run.PROJECT}-monitor"], check=False)
    metrics = (run.OUTPUT / "metrics-probe.jsonl").open("w")
    monitor = subprocess.Popen(
        [
            "docker",
            "run",
            "--rm",
            "--name",
            f"{run.PROJECT}-monitor",
            "--privileged",
            "--cgroupns=host",
            "--mount",
            "type=bind,source=/sys/fs/cgroup,target=/hostcg,readonly",
            "--mount",
            f"type=bind,source={run.ROOT},target=/bench,readonly",
            "--entrypoint",
            "python",
            run.ENV["APP_IMAGE"],
            "-u",
            "/bench/benchmarks/droplet_sizing/monitor.py",
        ],
        stdout=metrics,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        asyncio.run(workloads())
    finally:
        controls(1610612736, 100000, "0")
        (run.OUTPUT / "results-probe.json").write_text(json.dumps(run.RESULTS, indent=2))
        run.command(["docker", "stop", f"{run.PROJECT}-monitor"], check=False)
        monitor.wait(timeout=20)
        metrics.close()


if __name__ == "__main__":
    main()
