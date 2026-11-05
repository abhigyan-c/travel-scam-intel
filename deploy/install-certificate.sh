#!/usr/bin/env bash
# Certbot deploy hook; root only. Do not install staging certificates for public use.
set -euo pipefail
test "$(id -u)" = 0
test -n "${RENEWED_LINEAGE:?Certbot supplies the renewed certificate directory}"
install -d -o root -g caddy -m 0750 /etc/caddy/certs
install -o root -g caddy -m 0640 "$RENEWED_LINEAGE/fullchain.pem" /etc/caddy/certs/fullchain.pem
install -o root -g caddy -m 0640 "$RENEWED_LINEAGE/privkey.pem" /etc/caddy/certs/privkey.pem
systemctl reload caddy
