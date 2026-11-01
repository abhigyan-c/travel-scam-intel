#!/usr/bin/env bash
# Host dependencies: bws, python3, gpg, AWS CLI. Run as root via a nightly timer.
set -euo pipefail
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
test "$(id -u)" = 0
set -a
source /etc/travel-scam/backup.env
set +a
install -d -m 0700 /var/lib/travel-scam/backup-work
work="$(mktemp -d /var/lib/travel-scam/backup-work/job-XXXXXXXX)"
trap 'rm -rf -- "$work"' EXIT
python3 export_secrets.py "$BWS_BACKUP_PROJECT_ID" /etc/travel-scam/backup-machine-token "$work/secrets" --backup
export AWS_ACCESS_KEY_ID="$(cat "$work/secrets/S3_ACCESS_KEY")"
export AWS_SECRET_ACCESS_KEY="$(cat "$work/secrets/S3_SECRET_KEY")"
export AWS_DEFAULT_REGION=auto
object="postgres/$(date -u +%Y/%m/%d/%Y%m%dT%H%M%SZ).dump.gpg"
# No plaintext dump on disk. pipefail prevents uploading a partial dump.
docker exec travel-scam-prod-postgres-1 pg_dump -U travel -d travel_scam_intel -Fc |
  gpg --batch --yes --pinentry-mode loopback --passphrase-file "$work/secrets/BACKUP_PASSPHRASE" \
    --symmetric --cipher-algo AES256 --output "$work/database.dump.gpg"
aws --endpoint-url "$S3_ENDPOINT" s3 cp "$work/database.dump.gpg" "s3://$BACKUP_BUCKET/$object" --only-show-errors
install -d -m 0700 /var/lib/travel-scam
date -u +%FT%TZ > /var/lib/travel-scam/last-backup-success
