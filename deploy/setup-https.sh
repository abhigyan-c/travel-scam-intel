#!/usr/bin/env bash
# Root-only IP-first HTTPS preview. Keep administration on the tailnet.
set -euo pipefail
umask 077
test "$(id -u)" = 0
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
ip="${1:?Pass the Droplet public IPv4 address}"
python3 -c 'import ipaddress,sys; a=ipaddress.IPv4Address(sys.argv[1]); assert a.is_global' "$ip"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl python3-venv
curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key -o /tmp/travel-scam-caddy-key.asc
gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg /tmp/travel-scam-caddy-key.asc
rm -f /tmp/travel-scam-caddy-key.asc
curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt -o /etc/apt/sources.list.d/caddy-stable.list
chmod 0644 /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
apt-get update
apt-get install -y caddy
python3 -m venv /opt/travel-scam-certbot
/opt/travel-scam-certbot/bin/pip install 'certbot==5.4.0'
install -d -m 0755 /var/lib/travel-scam/acme
# The parent also holds protected backup state; traverse without listing it.
chmod 0711 /var/lib/travel-scam
install -d -m 0755 /etc/systemd/system/caddy.service.d
printf '[Service]\nEnvironment=VPS_IP=%s\n' "$ip" > /etc/systemd/system/caddy.service.d/travel-scam.conf
chmod 0644 /etc/systemd/system/caddy.service.d/travel-scam.conf
printf 'VPS_IP=%s\n' "$ip" > /etc/travel-scam/https.env
chmod 0600 /etc/travel-scam/https.env
systemctl daemon-reload
if ! test -s /etc/caddy/certs/fullchain.pem; then
  install -m 0644 Caddyfile.bootstrap /etc/caddy/Caddyfile
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  systemctl enable --now caddy
  systemctl reload caddy
fi
ufw allow 80/tcp comment 'ACME IP certificate validation'
ufw allow 443/tcp comment 'Public HTTPS MCP preview'
certbot=/opt/travel-scam-certbot/bin/certbot
# Separate ACME account/config tree keeps staging certificates out of production.
"$certbot" certonly --non-interactive --agree-tos --register-unsafely-without-email \
  --staging --config-dir /var/lib/travel-scam/acme-staging/config \
  --work-dir /var/lib/travel-scam/acme-staging/work --logs-dir /var/lib/travel-scam/acme-staging/logs \
  --webroot -w /var/lib/travel-scam/acme --preferred-profile shortlived \
  --ip-address "$ip" --cert-name ip-staging
"$certbot" certonly --non-interactive --agree-tos --register-unsafely-without-email \
  --webroot -w /var/lib/travel-scam/acme --preferred-profile shortlived \
  --ip-address "$ip" --cert-name vps-ip
install -d -o root -g caddy -m 0750 /etc/caddy/certs
install -o root -g caddy -m 0640 /etc/letsencrypt/live/vps-ip/fullchain.pem /etc/caddy/certs/fullchain.pem
install -o root -g caddy -m 0640 /etc/letsencrypt/live/vps-ip/privkey.pem /etc/caddy/certs/privkey.pem
install -m 0644 Caddyfile /etc/caddy/Caddyfile
VPS_IP="$ip" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl reload caddy
install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
install -m 0700 install-certificate.sh /etc/letsencrypt/renewal-hooks/deploy/travel-scam
install -m 0644 travel-scam-certbot.service travel-scam-certbot.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now travel-scam-certbot.timer
if ! grep -q '^PUBLIC_TLS_CERT=' /etc/travel-scam/production.env; then
  printf '\nPUBLIC_TLS_CERT=/etc/caddy/certs/fullchain.pem\n' >> /etc/travel-scam/production.env
fi
if ! grep -q '^PUBLIC_HTTPS_IP=' /etc/travel-scam/production.env; then
  printf 'PUBLIC_HTTPS_IP=%s\n' "$ip" >> /etc/travel-scam/production.env
fi
"$certbot" renew --cert-name vps-ip --dry-run --run-deploy-hooks
