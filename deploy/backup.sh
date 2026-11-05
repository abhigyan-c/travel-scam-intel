#!/usr/bin/env bash
# Host dependencies: bws, python3, gpg, AWS CLI. Run as root via a nightly timer.
set -euo pipefail
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
test "$(id -u)" = 0
exec 9>/run/lock/travel-scam-backup.lock
flock -n 9
set -a
source /etc/travel-scam/backup.env
set +a
install -d -m 0700 /var/lib/travel-scam/backup-work
work="$(mktemp -d /var/lib/travel-scam/backup-work/job-XXXXXXXX)"
install -d -m 0711 /run/travel-scam
runtime="$(mktemp -d /run/travel-scam/backup-XXXXXXXX)"
trap 'rm -rf -- "$work" "$runtime"' EXIT
python3 export_secrets.py "$BWS_BACKUP_PROJECT_ID" /etc/travel-scam/backup-machine-token "$runtime/secrets" --backup
export AWS_ACCESS_KEY_ID="$(cat "$runtime/secrets/S3_ACCESS_KEY")"
export AWS_SECRET_ACCESS_KEY="$(cat "$runtime/secrets/S3_SECRET_KEY")"
export AWS_DEFAULT_REGION=auto
object="postgres/$(date -u +%Y/%m/%d/%Y%m%dT%H%M%SZ).dump.gpg"
# No plaintext dump on disk. pipefail prevents uploading a partial dump.
docker exec travel-scam-prod-postgres-1 pg_dump -U travel -d travel_scam_intel -Fc |
  gpg --batch --yes --pinentry-mode loopback --passphrase-file "$runtime/secrets/BACKUP_PASSPHRASE" \
    --symmetric --cipher-algo AES256 --output "$work/database.dump.gpg"
aws --endpoint-url "$S3_ENDPOINT" s3api put-object --bucket "$BACKUP_BUCKET" \
  --key "$object" --body "$work/database.dump.gpg" --if-none-match '*' >/dev/null
install -d -m 0700 /var/lib/travel-scam
printf '%s\n' "$object" > /var/lib/travel-scam/last-backup-object
date -u +%FT%TZ > /var/lib/travel-scam/backup-success.tmp
mv /var/lib/travel-scam/backup-success.tmp /var/lib/travel-scam/last-backup-success
