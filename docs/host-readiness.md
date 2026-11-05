# Droplet and tailnet readiness — 2026-11-03

Status update (2026-11-05): the private API, worker, PostgreSQL and Redis stack is
deployed and healthy. The dedicated deploy key successfully pushed and activated
the immutable release; the embedding model is preloaded. A real encrypted R2
backup restored migration 0021 and all 31 public tables into an isolated database.
See [PR 38 rollout](pr38-rollout.md) for current evidence and remaining gates.
This is not a public production-release sign-off. The dated preflight below is
retained as the initial baseline.

## Provisioned host and access

- DigitalOcean Bangalore (`blr1`), x86-64, 2 vCPUs, 3,915 MiB reported RAM and a
  77-GiB root filesystem (the intended 4-GiB / 80-GB class). Billing was not inspected.
- Ubuntu Server 24.04.5 LTS; clock synchronized; no failed systemd services or
  reboot-required flag during initial inspection. Nineteen package upgrades were
  available; applying the OS patch baseline belongs to the initial PR 38 setup.
- Installed official Tailscale 1.104.5 and registered `travel-scam-prod` in the
  user's existing tailnet. The Windows PC already had a running Tailscale client.
- `ssh travel-scam-prod` on the operator PC now uses the private tailnet address.
  Private IP/MagicDNS details are in the operator's local SSH configuration, not
  committed deployment secrets. SSH host keys were pinned from the already trusted
  public-IP connection; no unchecked host-key replacement was accepted.
- Verified private SSH before and after enabling UFW. Public TCP 22 was then
  tested and blocked. UFW denies inbound traffic by default, permits SSH on
  `tailscale0`, and permits UDP 41641 for Tailscale transport. Outbound is allowed.
- Existing OpenSSH password and keyboard-interactive authentication are disabled;
  key-based root administration remains available over the tailnet. A dedicated
  deployment user/key remains part of PR 38 setup.
- Tailscale is enabled at boot and reports no health errors. Node key expiry was
  reported as 2027-05-02T15:52:06Z; reauthenticate before expiry. Expiry was not disabled.
- No Tailscale Serve/Funnel endpoint has been enabled. The public address still
  exists for internet transport/egress; administrative connections use the overlay.
- Reboot drill passed: private SSH recovered after about 17 seconds, Tailscale and
  UFW remained active/enabled, public TCP 22 remained blocked and systemd had no
  failed services. Post-reboot RAM use was 458 MiB before Docker/application setup,
  with 3,456 MiB available and about 75 GiB disk available. No swap was configured.

## Live integration checks

| System | Evidence | Result |
| --- | --- | --- |
| Bitwarden `prod-app` | Native CLI on Droplet retrieved 8 nonempty secrets | Passed |
| Bitwarden `prod-backup` | Native CLI on Droplet retrieved 3 nonempty secrets | Passed |
| R2 application bucket | Signed upload/download, conditional overwrite rejected, temporary object removed and absence checked, from Droplet | Passed |
| R2 backup bucket | Same native signed checks, from Droplet | Passed |
| Serper | One English search from Droplet; HTTP 200 and one result | Passed |
| Sarvam | One minimal request from Droplet; HTTP 200, `sarvam-105b`, choices present | Passed |
| Outbound DNS/TLS | R2, Bitwarden API/identity, container registry and GitHub reachable with certificate verification | Passed |
| R2 public exposure | User confirmed both public-development URLs disabled and no enabled custom domains | Confirmed by user |

Cloudflare management API inspection still returns HTTP 403 with the supplied
token. The user's dashboard confirmation is recorded separately from independent
API verification; unauthenticated S3 denial alone is not proof about public domains.

Credentials were passed over encrypted stdin/environment, not printed or copied
into a server `.env`. Temporary verification code, dependencies, CLI and scratch
directories were removed. Synthetic R2 objects were cleaned up. No application data
or invented scam records were created. Existing vault entries were not changed.

## Remaining PR 38 implementation and release gates

1. Install/configure Docker/Compose, Caddy as appropriate for the chosen endpoint,
   production secrets delivery, model cache, PostgreSQL/pgvector, Redis, API and worker.
2. Connect GitHub Actions to the tailnet with a scoped ephemeral identity before
   using the private host as `VPS_HOST`; a hosted runner cannot reach the tailnet
   merely because the operator PC can. Configure the dedicated deploy key, pinned
   host identity and production environment. Keep deployment push-based.
3. Choose the ChatGPT-facing route. Private tailnet membership does not make the
   MCP endpoint reachable by ChatGPT. Use public trusted HTTPS (potentially a
   separately authorized Tailscale Funnel hostname) or a supported Secure MCP
   Tunnel. Do not reopen public SSH or silently enable Funnel for this purpose.
4. Perform real deployment, reboot/service recovery, encrypted offsite backup,
   isolated restore and rollback drills. Check actual host memory under load.
5. Resolve the measured image-memory, matching-backpressure and grown-corpus
   `recent` query issues before public release, then resume PR 31's live MCP gate.

References: [private CI networking](https://tailscale.com/docs/solutions/connect-github-cicd-workflows-to-private-infrastructure-without-public-exposure),
[ChatGPT endpoint requirements](https://developers.openai.com/plugins/deploy/connect-chatgpt),
[sizing experiment](digitalocean-sizing-experiment.md).
