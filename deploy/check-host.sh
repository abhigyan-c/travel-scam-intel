#!/usr/bin/env bash
# Wire nonzero exit status to the operator's chosen alerting service.
set -euo pipefail
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8000/ready >/dev/null
test -f /var/lib/travel-scam/last-backup-success
last="$(date -d "$(cat /var/lib/travel-scam/last-backup-success)" +%s)"
test "$(( $(date +%s) - last ))" -lt 93600
test "$(df --output=pcent /var/lib/docker | tail -1 | tr -dc '0-9')" -lt 85
openssl x509 -checkend 172800 -noout -in /etc/caddy/certs/fullchain.pem >/dev/null
