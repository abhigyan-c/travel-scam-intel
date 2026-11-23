#!/usr/bin/env bash
# Execute as root on the VPS after loading the release image.
set -euo pipefail
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
test "$(id -u)" = 0
release="${1:?Pass a full commit SHA}"
[[ "$release" =~ ^[a-f0-9]{40}$ ]]
exec 8>/run/lock/travel-scam-activate.lock
flock -n 8
set -a
source /etc/travel-scam/production.env
set +a
export APP_IMAGE="travel-scam-intel:$release"
export POSTGRES_IMAGE="travel-scam-postgres:$release"
# Fail before touching healthy services if the selected release is unavailable.
docker image inspect "$APP_IMAGE" >/dev/null
docker image inspect "$POSTGRES_IMAGE" >/dev/null
test -d "$MODEL_CACHE"
install -d -m 0711 /run/travel-scam
export SECRETS_PATH="$(mktemp -d /run/travel-scam/export-XXXXXXXX)"
rmdir "$SECRETS_PATH"
python3 export_secrets.py "$BWS_APP_PROJECT_ID" /etc/travel-scam/app-machine-token "$SECRETS_PATH"
compose=(docker compose --env-file /dev/null -f compose.yaml)
"${compose[@]}" config --quiet
# Stop the old app while applying migrations. Do not automatically downgrade schemas.
"${compose[@]}" stop worker api
# Compose does not hash file-secret source paths into container configuration.
# Recreate secret consumers so reboot/rotation never reuses a vanished /run mount.
"${compose[@]}" up -d --wait --force-recreate postgres
"${compose[@]}" up -d --wait redis
"${compose[@]}" run --rm --no-deps api alembic upgrade head
"${compose[@]}" run --rm --no-deps api python -m app.db.seed
"${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 180 api
"${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 180 worker
curl --fail --silent --show-error http://127.0.0.1:8000/ready >/dev/null
printf '%s\n' "$release" > /opt/travel-scam/active-release
ln -sfn "/opt/travel-scam/releases/$release" /opt/travel-scam/current
