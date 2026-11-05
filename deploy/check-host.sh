#!/usr/bin/env bash
# Wire nonzero exit status to the operator's chosen alerting service.
set -euo pipefail
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8000/ready >/dev/null
test -f /var/lib/travel-scam/last-backup-success
last="$(date -d "$(cat /var/lib/travel-scam/last-backup-success)" +%s)"
test "$(( $(date +%s) - last ))" -lt 93600
test "$(df --output=pcent /var/lib/docker | tail -1 | tr -dc '0-9')" -lt 85
# Private preview has no public certificate. Require certificate monitoring when
# the operator configures a public TLS endpoint in production.env.
source /etc/travel-scam/production.env
if [[ -n "${PUBLIC_TLS_CERT:-}" ]]; then
  openssl x509 -checkend 172800 -noout -in "$PUBLIC_TLS_CERT" >/dev/null
fi
if [[ -n "${PUBLIC_HTTPS_IP:-}" ]]; then
  # Connect to the actual local public-IP interface: IP-literal clients omit SNI,
  # so connecting to 127.0.0.1 would select the wrong TLS certificate identity.
  curl --fail --silent --show-error --max-time 10 "https://$PUBLIC_HTTPS_IP/health" >/dev/null
  # Check the certificate actually served, including when a deploy hook failed
  # after copying new files but before reloading Caddy.
  served="$(timeout 10 openssl s_client -connect "$PUBLIC_HTTPS_IP:443" -servername "$PUBLIC_HTTPS_IP" \
    -verify_ip "$PUBLIC_HTTPS_IP" -verify_return_error -CApath /etc/ssl/certs \
    </dev/null 2>/dev/null)"
  openssl x509 -checkend 172800 -noout <<< "$served" >/dev/null
fi
