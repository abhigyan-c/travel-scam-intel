"""Run a bounded source probe in a separate existing API-container process over SSH.

Loads candidate modules in process memory; does not deploy or write on the server.
Uses the container's existing Serper credential without exporting it.
"""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

MODULES = {
    "app.integrations.fetch": "app/integrations/fetch.py",
    "app.acquisition": "app/acquisition.py",
    "app.discovery": "app/discovery.py",
    "bench_probe": "benchmarks/acquisition.py",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="travel-scam-prod")
    parser.add_argument("--container", default="travel-scam-prod-api-1")
    parser.add_argument("--location", default="Bangkok, Thailand")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live or args.output.exists():
        raise ValueError("explicit_live_and_new_output_required")
    if not all(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) for value in (args.host, args.container)
    ):
        raise ValueError("invalid_ssh_alias_or_container")
    sources = {name: Path(path).read_text(encoding="utf-8") for name, path in MODULES.items()}
    code = "import sys,types,json,asyncio\n"
    code += "sources=" + repr(sources) + "\n"
    code += "location=" + repr(args.location) + "\n"
    code += """for name,source in sources.items():
 module=types.ModuleType(name);module.__file__='/app/'+name.replace('.','/')+'.py'
 sys.modules[name]=module
 exec(compile(source,module.__file__,'exec'),module.__dict__)
from types import SimpleNamespace
async def main():
 report=await sys.modules['bench_probe'].probe(SimpleNamespace(live=True,location=location))
 print('RESULT_JSON='+json.dumps(report))
asyncio.run(main())
"""
    result = subprocess.run(
        ["ssh", args.host, "docker", "exec", "-i", args.container, "python"],
        input=code,
        capture_output=True,
        text=True,
        timeout=100,
        check=True,
    )
    line = next(line for line in result.stdout.splitlines() if line.startswith("RESULT_JSON="))
    report = json.loads(line.removeprefix("RESULT_JSON="))
    report.update(
        environment="Existing Droplet, isolated process; running service unchanged.",
        source_hashes={
            MODULES[name]: hashlib.sha256(source.encode()).hexdigest()
            for name, source in sources.items()
        },
        commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        working_tree_dirty=bool(subprocess.check_output(["git", "diff", "--name-only"], text=True)),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "discovery_seconds": report["discovery_seconds"],
                "fetch_attempts": len(report["observations"]),
            }
        )
    )


if __name__ == "__main__":
    main()
