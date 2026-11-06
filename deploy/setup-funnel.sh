#!/usr/bin/env bash
# Prepare the filtered listener/allowlist; --enable requires explicit user consent.
set -euo pipefail
umask 077
test "$(id -u)" = 0
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
mode="${1:---prepare-only}"
[[ "$mode" = --prepare-only || "$mode" = --enable ]]
fqdn="$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
python3 - "$fqdn" <<'PY'
import json
import re
import shlex
import sys
from pathlib import Path

hostname = sys.argv[1]
assert re.fullmatch(r"[a-z0-9.-]+\.ts\.net", hostname)
path = Path('/etc/travel-scam/production.env')
lines = path.read_text().splitlines()
indices = [i for i, line in enumerate(lines) if line.startswith('MCP_PUBLIC_ALIASES=')]
assert len(indices) <= 1
urls = json.loads(shlex.split(lines[indices[0]].split('=', 1)[1])[0]) if indices else []
url = 'https://' + hostname + '/mcp'
if url not in urls:
    urls.append(url)
line = 'MCP_PUBLIC_ALIASES=' + shlex.quote(json.dumps(urls))
if indices:
    lines[indices[0]] = line
else:
    lines.append(line)
path.write_text('\n'.join(lines) + '\n')
path.chmod(0o600)
print('Prepared MCP alias: ' + url)
PY
set -a
source /etc/travel-scam/https.env
set +a
caddy validate --config Caddyfile --adapter caddyfile >/dev/null
install -m 0644 Caddyfile /etc/caddy/Caddyfile
systemctl reload caddy
curl --fail --silent --show-error http://127.0.0.1:8080/health >/dev/null
test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8080/v1/admin/emerging-patterns)" = 404
if [[ "$mode" = --enable ]]; then
  # The user must authorize this public endpoint separately from private tailnet access.
  tailscale funnel --bg http://127.0.0.1:8080
else
  printf '%s\n' 'Listener prepared; deploy the MCP alias settings before enabling Funnel.'
fi
