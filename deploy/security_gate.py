"""Fail deployment on patchable high/critical vulnerabilities in full Trivy reports."""

import json
import sys
from pathlib import Path


def check_report(path: Path) -> bool:
    report = json.loads(path.read_text(encoding="utf-8"))
    results = report.get("Results")
    if report.get("SchemaVersion") != 2 or not results:
        raise ValueError(f"Missing or unsupported scan results: {path}")
    vulnerabilities = [v for result in results for v in result.get("Vulnerabilities", [])]
    blocked = [
        v
        for v in vulnerabilities
        if v.get("FixedVersion") and v["Severity"] in {"HIGH", "CRITICAL"}
    ]
    print(f"{path}: {len(vulnerabilities)} total findings; {len(blocked)} blocking findings")
    for finding in blocked:
        print(f"  {finding['VulnerabilityID']} {finding['PkgName']} -> {finding['FixedVersion']}")
    return not blocked


def main() -> int:
    if len(sys.argv) < 2:
        raise ValueError("At least one scan report is required")
    results = [check_report(Path(path)) for path in sys.argv[1:]]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
