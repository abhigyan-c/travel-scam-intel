#!/usr/bin/env bash
# Certbot deploy hook; root only. Do not install staging certificates for public use.
set -euo pipefail
test "$(id -u)" = 0
test -n "${RENEWED_LINEAGE:?Certbot supplies the renewed certificate directory}"
# Only the production IP lineage may replace the live certificate. A staging
# dry run or an unrelated lineage must never install an untrusted certificate.
test "$RENEWED_LINEAGE" = /etc/letsencrypt/live/vps-ip
set -a
source /etc/travel-scam/https.env
set +a
openssl x509 -checkend 172800 -noout -in "$RENEWED_LINEAGE/fullchain.pem" >/dev/null
openssl x509 -checkip "$VPS_IP" -noout -in "$RENEWED_LINEAGE/fullchain.pem" >/dev/null
openssl verify -CApath /etc/ssl/certs \
  -untrusted "$RENEWED_LINEAGE/chain.pem" "$RENEWED_LINEAGE/cert.pem" >/dev/null
install -d -o root -g caddy -m 0750 /etc/caddy/certs
install -o root -g caddy -m 0640 "$RENEWED_LINEAGE/fullchain.pem" /etc/caddy/certs/fullchain.pem
install -o root -g caddy -m 0640 "$RENEWED_LINEAGE/privkey.pem" /etc/caddy/certs/privkey.pem
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
systemctl reload caddy
