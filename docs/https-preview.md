# Public HTTPS preview

The user authorized IP-first HTTPS on 2026-11-05. Use the existing Droplet public
IP with a free Let's Encrypt short-lived certificate; no domain or load balancer.
Administration and deployment remain on Tailscale. External alert delivery was
explicitly deferred by the user on 2026-11-05; retain it as a release follow-up.

## Configuration

Run `bash deploy/setup-https.sh PUBLIC_IPV4` as root over the verified SSH alias.
It installs Caddy from the signed official repository and Certbot 5.4.0 in an
isolated environment, enables TCP 80/443, and validates issuance against staging
before requesting the production certificate. The ACME account is registered
without an email address; it does not provide an alerting channel.

Staging uses a separate configuration/account tree and is never installed into
Caddy. The production lineage is `/etc/letsencrypt/live/vps-ip`; its deploy hook
checks the lineage, IP SAN, remaining validity and trusted chain before installing
root:caddy 0640 certificate/key files, validating Caddy and reloading it.
The systemd drop-in supplies Caddy's `VPS_IP`; `/etc/travel-scam/https.env` supplies
the same nonsecret metadata to the root-only deploy hook.

- Public HTTPS `/health` proxies process health.
- `/mcp` and `/mcp/*` proxy to the application; PR 31 now implements the exact
  `/mcp` endpoint. Protocol initialization passed; the real ChatGPT gate is pending.
- All other HTTPS routes return proxy-level 404, including REST, admin, media,
  readiness, API documentation and OpenAPI. They remain available via private SSH
  forwarding when needed. Public upload exposure must wait for PR 37 safeguards.
- HTTP serves only `/.well-known/acme-challenge/*`; other paths return 503.
- Request bodies are capped at 12 MB; Caddy access logging is not enabled, avoiding
  request/query logging. No database, Redis or public SSH ports are opened.

`/var/lib/travel-scam` permits traversal without listing (0711) so Caddy can serve
the public challenge directory (0755). Backup work directories stay 0700 and
marker files 0600. Backup/bootstrap scripts must preserve that traversal mode.

## Renewal and failure checks

`travel-scam-certbot.timer` runs renewal twice daily, with randomized delay and
persistent catch-up. The installer runs a staging renewal dry run with deploy hooks.
Certbot's additional random sleep is disabled because the systemd timer already
provides jitter; the service timeout covers the actual renewal work.
Short-lived IP certificates are valid for about six days, so renewal is required.

`PUBLIC_TLS_CERT` and `PUBLIC_HTTPS_IP` in the root-only production configuration
enable stored/served certificate expiry checking (48-hour threshold) and a verified local HTTPS probe in the
hourly health service. Renewal and health failures appear in systemd/journal.
External notifications are **not configured**; a failed server cannot notify its
operator using these local checks alone. Revisit Healthchecks.io/email before
public release; no additional monitoring account was provisioned in this change.

Operator commands:

```sh
systemctl status caddy travel-scam-certbot.timer travel-scam-health.timer
/opt/travel-scam-certbot/bin/certbot renew --cert-name vps-ip --dry-run --run-deploy-hooks --no-random-sleep-on-renew
systemctl start travel-scam-health.service
journalctl -u travel-scam-certbot.service -u travel-scam-health.service
```

Changing the Droplet public IP requires new issuance and updating Caddy/hook/health
metadata. Preserve the existing address or update all three together.

Sources: [Caddy installation](https://caddyserver.com/docs/install),
[Let's Encrypt IP certificates](https://letsencrypt.org/2026/03/11/shorter-certs-certbot).

## Live validation — 2026-11-05

- Staging issuance succeeded before trusted production issuance.
- An external PC verified the trusted chain and IP SAN at `https://64.227.161.120`.
  The first certificate expires 2026-11-12 02:54:16 IST (2026-11-11 21:24:16 UTC).
- `/health` returned HTTP 200; seven private routes returned proxy-level 404;
  anonymous REST report POST was blocked; HTTP application access returned 503.
- `/mcp` reached the backend and returned application 404, as expected before PR 31.
- The production certificate install/reload hook passed; staging renewal simulation
  passed including deploy hooks. The twice-daily timer is enabled; a manual run of
  the renewal service passed. External alert delivery remains deferred.
- Caddy 2.11.9 and Certbot 5.4.0 are installed. The host health check verifies the
  actual served certificate has at least 48 hours remaining, even if a future
  reload fails after copying new certificate files.
- A real encrypted R2 backup preserved ACME webroot traversal; an external PC then
  retrieved a unique synthetic HTTP challenge and the file was removed afterward.
- The staging lineage was rejected by the deploy hook without changing the live
  certificate. Encoded/traversal-style admin paths also returned HTTP 404.
- The updated host-helper release `a69fe8b` is active; the application source/image
  was unchanged. The root-owned renewal unit was updated separately to use the
  systemd timer's jitter without Certbot's additional internal random sleep.
- Deployment/service unit tests: 12 passed. Ruff lint/format and diff checks passed.

Re-run external boundary checks with
`python -m deploy.verify_https PUBLIC_IPV4`; it now checks MCP initialization.
