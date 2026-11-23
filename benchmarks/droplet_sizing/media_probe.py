"""Largest allowed decoded images, one then five concurrent uploads, synthetic only."""

import argparse
import asyncio
import io
import json
import subprocess
import time
from uuid import uuid4

import httpx
from PIL import Image

from benchmarks.droplet_sizing import run
from benchmarks.droplet_sizing.control import controls


async def uploads(label, count, image):
    run.phase(label)
    async with httpx.AsyncClient(base_url="http://127.0.0.1:18012", timeout=40) as client:
        reports = []
        for _index in range(count):
            key = f"sizing-media-{uuid4()}"
            response = await client.post(
                "/v1/reports",
                json={
                    "report_text": (
                        "Synthetic sizing report. I agreed a taxi fare "
                        "but was asked for extra money."
                    ),
                    "location_id": run.location(50),
                    "provenance_class": "FIRST_HAND",
                },
                headers={"Idempotency-Key": key},
            )
            response.raise_for_status()
            reports.append((response.json()["report_id"], key))

        async def upload(report):
            before = time.perf_counter()
            try:
                response = await client.post(
                    f"/v1/reports/{report[0]}/attachments",
                    content=image,
                    headers={
                        "Content-Type": "image/png",
                        "Idempotency-Key": f"media-{uuid4()}",
                        "Report-Submission-Key": report[1],
                    },
                )
                status = str(response.status_code)
            except Exception as exc:
                status = type(exc).__name__
            return {"status": status, "seconds": time.perf_counter() - before}

        outcomes = await asyncio.gather(*(upload(report) for report in reports))
    row = {
        "phase": label,
        "concurrency": count,
        "image_pixels": 10_000_000,
        "compressed_bytes": len(image),
        "outcomes": outcomes,
    }
    run.record(row)
    print(json.dumps(row), flush=True)
    await asyncio.sleep(5)


async def workloads(memory_only=False):
    data = io.BytesIO()
    Image.new("RGBA", (4000, 2500), (10, 20, 30, 255)).save(data, format="PNG")
    image = data.getvalue()
    if memory_only:
        controls(3758096384, 100000, "0")
        await run.requests("memory_only_control_warm_embedding", 1, match=True)
        await uploads("4GiB_1cpu_memory_only_control_5uploads", 5, image)
        run.phase("memory_only_control_done")
        return
    controls(1610612736, 100000, "0")
    await run.requests("media_2GiB_warm_embedding_before_upload", 1, match=True)
    await uploads("2GiB_one_10MP_upload", 1, image)
    await uploads("2GiB_five_10MP_uploads", 5, image)
    controls(3758096384, 200000, "0-1")
    await run.requests("media_4GiB_warm_embedding_before_upload", 1, match=True)
    await uploads("4GiB_five_10MP_uploads_control", 5, image)
    controls(1610612736, 100000, "0")
    run.phase("media_probe_done")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--memory-only", action="store_true")
    args = parser.parse_args()
    suffix = "media-memory-only" if args.memory_only else "media"
    metrics = (run.OUTPUT / f"metrics-{suffix}.jsonl").open("w")
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
        asyncio.run(workloads(args.memory_only))
    finally:
        controls(1610612736, 100000, "0")
        (run.OUTPUT / f"results-{suffix}.json").write_text(json.dumps(run.RESULTS, indent=2))
        run.command(["docker", "stop", f"{run.PROJECT}-monitor"], check=False)
        monitor.wait(timeout=20)
        metrics.close()


if __name__ == "__main__":
    main()
