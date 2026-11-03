"""Run outside the capped group; records aggregate cgroup v2 accounting."""

import json
import time
from pathlib import Path

ROOT = Path("/hostcg/travel-scam-sizing-20261103")


def pairs(path):
    return {
        key: int(value) for key, value in (line.split() for line in path.read_text().splitlines())
    }


while True:
    try:
        row = {
            "time": time.time(),
            "memory": int((ROOT / "memory.current").read_text()),
            "peak": int((ROOT / "memory.peak").read_text()),
            "swap": int((ROOT / "memory.swap.current").read_text()),
            "events": pairs(ROOT / "memory.events"),
            "cpu": pairs(ROOT / "cpu.stat"),
            "stat": pairs(ROOT / "memory.stat"),
            "children": {},
        }
        for child in ROOT.iterdir():
            if child.is_dir() and (child / "memory.current").exists():
                row["children"][child.name] = {
                    "memory": int((child / "memory.current").read_text()),
                    "stat": pairs(child / "memory.stat"),
                }
        print(json.dumps(row), flush=True)
    except FileNotFoundError:
        pass
    time.sleep(0.25)
