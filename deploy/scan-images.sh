#!/usr/bin/env bash
# Retain full inventories, then reject vendor-fixable HIGH/CRITICAL findings.
# Unfixed findings remain visible for review; they are not silently suppressed.
set -euo pipefail
test "$#" -gt 0
scanner=aquasec/trivy@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb
mkdir -p security-reports
index=0
for image in "$@"; do
  index=$((index + 1))
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$PWD/security-reports:/reports" "$scanner" image \
    --scanners vuln --format json --output "/reports/image-$index.json" "$image"
done
python3 -m deploy.security_gate security-reports/image-*.json
