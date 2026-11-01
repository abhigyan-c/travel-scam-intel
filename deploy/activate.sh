#!/usr/bin/env bash
# Execute as root on the VPS after loading the release image.
set -euo pipefail
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
test "$(id -u)" = 0
release="${1:?Pass a full commit SHA}"
[[ "$release" =~ ^[a-f0-9]{40}$ ]]
set -a
source /etc/travel-scam/production.env
set +a
export APP_IMAGE="travel-scam-intel:$release"
install -d -m 0711 /run/travel-scam
export SECRETS_PATH="$(mktemp -d /run/travel-scam/export-XXXXXXXX)"
rmdir "$SECRETS_PATH"
python3 export_secrets.py "$BWS_APP_PROJECT_ID" /etc/travel-scam/app-machine-token "$SECRETS_PATH"
compose=(docker compose --env-file /dev/null -f compose.yaml)
"${compose[@]}" config --quiet
"${compose[@]}" up -d --wait postgres redis
# Stop the old app while applying migrations. Do not automatically downgrade schemas.
"${compose[@]}" stop worker api
"${compose[@]}" run --rm --no-deps api alembic upgrade head
"${compose[@]}" run --rm --no-deps api python -m app.db.seed
"${compose[@]}" up -d --wait --wait-timeout 180 api worker
curl --fail --silent --show-error http://127.0.0.1:8000/ready >/dev/null
printf '%s\n' "$release" > /opt/travel-scam/active-release
ln -sfn "/opt/travel-scam/releases/$release" /opt/travel-scam/current
