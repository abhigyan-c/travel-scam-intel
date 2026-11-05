#!/usr/bin/env bash
# Download the most recent successful backup and restore only to a disposable DB.
set -euo pipefail
umask 077
test "$(id -u)" = 0
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
set -a
source /etc/travel-scam/backup.env
set +a
object="$(cat /var/lib/travel-scam/last-backup-object)"
[[ "$object" = postgres/*.dump.gpg ]]
work="$(mktemp -d /var/lib/travel-scam/backup-work/verify-XXXXXXXX)"
runtime="$(mktemp -d /run/travel-scam/verify-XXXXXXXX)"
trap 'rm -rf -- "$work" "$runtime"' EXIT
python3 export_secrets.py "$BWS_BACKUP_PROJECT_ID" /etc/travel-scam/backup-machine-token "$runtime/secrets" --backup
export AWS_ACCESS_KEY_ID="$(cat "$runtime/secrets/S3_ACCESS_KEY")"
export AWS_SECRET_ACCESS_KEY="$(cat "$runtime/secrets/S3_SECRET_KEY")"
export AWS_DEFAULT_REGION=auto
aws --endpoint-url "$S3_ENDPOINT" s3 cp "s3://$BACKUP_BUCKET/$object" "$work/database.dump.gpg" --only-show-errors
bash restore-drill.sh "$work/database.dump.gpg" "$runtime/secrets/BACKUP_PASSPHRASE"
