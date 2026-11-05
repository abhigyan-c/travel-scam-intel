#!/usr/bin/env bash
# Root-owned host entry point. Deployment keys confer privileged code execution.
set -euo pipefail
test "$(id -u)" = 0
release="${1:?Pass the full commit SHA}"
[[ "$release" =~ ^[a-f0-9]{40}$ ]]
test "$#" = 1
exec 9>/run/lock/travel-scam-deploy.lock
flock -n 9
cd "/opt/travel-scam/releases/$release"
docker load --input image.tar.gz
tar -xzf deployment.tar.gz
bash deploy/activate.sh "$release"
# Keep the immutable image for rollback; the transfer archive is redundant.
rm -f image.tar.gz
