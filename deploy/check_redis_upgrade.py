"""Check AOF persistence across the Redis security-image upgrade in isolation."""

import argparse
import json
import subprocess
import time
from uuid import uuid4

OLD_IMAGE = (
    "redis:7.4-alpine@sha256:7f7d30b7fdd09467bac58198a1f166dd69789d348d4fe859cccb20e0cf287fbe"
)


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, timeout=120).strip()


def run(image):
    name = "tsi-redis-check-" + uuid4().hex[:12]
    volume = name + "-data"
    docker("volume", "create", volume)
    try:
        for selected in [OLD_IMAGE, image]:
            docker(
                "run",
                "-d",
                "--name",
                name,
                "--network",
                "none",
                "-v",
                volume + ":/data",
                selected,
                "redis-server",
                "--appendonly",
                "yes",
                "--appendfsync",
                "always",
            )
            for _ in range(60):
                try:
                    if docker("exec", name, "redis-cli", "ping") == "PONG":
                        break
                except subprocess.CalledProcessError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError("Redis did not become ready")
            if selected == OLD_IMAGE:
                assert (
                    docker("exec", name, "redis-cli", "SET", "security-check", "retained") == "OK"
                )
                docker("exec", name, "redis-cli", "ZADD", "queue-check", "123", "job-id")
            else:
                assert docker("exec", name, "redis-cli", "GET", "security-check") == "retained"
                assert docker("exec", name, "redis-cli", "ZSCORE", "queue-check", "job-id") == "123"
            docker("stop", name)
            docker("rm", name)
        print(json.dumps({"redis_aof_upgrade": "passed", "key_and_queue_preserved": True}))
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        docker("volume", "rm", volume)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    run(parser.parse_args().image)
