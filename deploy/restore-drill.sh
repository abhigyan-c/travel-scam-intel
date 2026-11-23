#!/usr/bin/env bash
# Restore an encrypted dump into a disposable, network-isolated database.
set -euo pipefail
umask 077
dump="$(realpath "${1:?Pass the downloaded encrypted dump}")"
passphrase="$(realpath "${2:?Pass a protected passphrase file}")"
test -f "$dump"
test -f "$passphrase"
if [[ -z "${POSTGRES_IMAGE:-}" ]]; then
  release="$(cat /opt/travel-scam/active-release)"
  [[ "$release" =~ ^[a-f0-9]{40}$ ]]
  POSTGRES_IMAGE="travel-scam-postgres:$release"
fi
docker image inspect "$POSTGRES_IMAGE" >/dev/null
name="travel-scam-restore-$(date +%s)-$$"
trap 'docker rm -fv "$name" >/dev/null 2>&1 || true' EXIT
docker run -d --name "$name" --network none \
  -e POSTGRES_USER=travel -e POSTGRES_DB=restore \
  -e POSTGRES_HOST_AUTH_METHOD=trust "$POSTGRES_IMAGE" >/dev/null
ready=false
for attempt in {1..60}; do
  if docker exec "$name" pg_isready -U travel -d restore >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
test "$ready" = true
gpg --batch --pinentry-mode loopback --passphrase-file "$passphrase" --decrypt "$dump" |
  docker exec -i "$name" pg_restore -U travel -d restore --exit-on-error --no-owner
docker exec "$name" psql -U travel -d restore -v ON_ERROR_STOP=1 \
  -c 'SELECT version_num FROM alembic_version' \
  -c "SELECT count(*) AS restored_tables FROM information_schema.tables WHERE table_schema = 'public'"
printf '%s\n' 'Isolated SQL restore succeeded. Application/provenance/object checks remain required.'
