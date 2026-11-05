# PR 38 private hosting rollout — 2026-11-05

Branch: `codex/pr-38-vps-foundation`, based on `develop` at `2262828`.
This completes the early private-hosting slice before the deferred PR 31 MCP work;
public release still depends on the remaining product and hardening PRs.

## Implemented

- Updated Ubuntu 24.04 on the DigitalOcean Bangalore 2-vCPU/4-GiB Droplet.
- Installed Docker 29.9.3, Compose 5.7.0, checksum-verified Bitwarden CLI 2.1.0,
  GnuPG and isolated AWS CLI 1.42.0. No load balancer or managed database.
- Created a dedicated `deploy` user and SSH key, separate from the operator's key.
  Its sudo entry calls a root-owned release wrapper; deploying code is privileged.
- Installed root-only machine-token files and nonsecret host configuration.
  App secrets are exported from Bitwarden to runtime files, never embedded in CI
  artifacts or container images. The supplied token and R2 key still span both
  projects/buckets; narrower credentials have not been supplied.
- GitHub's manually dispatched push workflow joins Tailscale using OIDC, verifies
  the pinned SSH host key, transfers an image tagged with the selected commit,
  and serializes activation. No repository key or registry account on the VPS.
- Deployed PostgreSQL/pgvector, Redis, read-only API and worker containers. API
  listens only on host loopback; database and Redis have no host port mappings.
  The pinned embedding model is preloaded on disk and mounted read-only.
- Added serialized encrypted, conditionally created R2 backups; automated
  download/isolated restore; boot recovery; nightly backup and hourly health timers.
  Missing images fail activation before stopping a healthy application.
  Secret-consuming containers are recreated during activation because Compose
  does not detect a changed file-secret source path. This also restarts PostgreSQL;
  deployments have a short maintenance window rather than zero downtime.

## Live evidence

- Dedicated-key push/activation succeeded for commit `2ab3876`; API and worker
  health checks passed with zero restarts and no OOM kills.
- `/ready` verified PostgreSQL, Redis and private R2 storage; migration is `0021`.
- Initial idle container memory: API 106.8 MiB, worker 91.1 MiB, PostgreSQL
  41.1 MiB, Redis 3.4 MiB. This is idle before embeddings load into serving
  processes, not proof of load capacity. See the earlier sizing experiment.
- An actual database dump was encrypted before uploading to the private backup
  bucket, downloaded with backup credentials, and restored into a disposable
  network-isolated PostgreSQL container. Migration 0021 and 31 tables recovered;
  the disposable container and volume were removed.
- The first application reboot drill exposed stale `/run` secret mounts. Activation
  now recreates PostgreSQL/API/worker against newly exported files; the boot unit
  also retries transient startup failures. The repeat reboot recovered the full
  application automatically in **57 seconds**, with no failed systemd units.
- Missing-image activation failed before stopping services and preserved the active
  marker. Rollback to `2ab3876` and reactivation of `5e04a07` both passed; the latter
  is the running code release. These images share the same application/schema, so
  this does not establish compatibility across future destructive migrations.
- After reboot: API/worker healthy, zero restarts/OOM kills; host memory used
  740 MiB with 3,175 MiB available; disk 4.6 GiB used, 72 GiB available, no swap.
  Public TCP ports 22, 80, 443, 5432, 6379 and 8000 were unreachable from the PC.
  Nightly backup and hourly health timers are enabled; the health unit passed.
- Local full suite against local PostgreSQL/Redis/MinIO: **828 passed, 10 skipped**.
  Ruff lint/format, Bash syntax, production Compose configuration and tracked-file
  scans for the supplied credential values passed. Production data was not used
  by the local test suite.
- Post-reboot read-only probe: 100 sequential empty-corpus location reads all
  succeeded, p50 26.87 ms / p95 46.75 ms (not representative of a grown corpus).
  The unresearched location remained `research_incomplete`, incident matching
  returned `no_match`, and anonymous admin access returned HTTP 401. No research
  was queued. Offline inference inside the read-only API container produced a
  normalized 384-dimensional embedding without downloading models.
- Only seed locations exist in this new database; no fabricated scam evidence or
  test reports were inserted into production. The restore drill does not prove
  recovery of future user media or provenance; verify those after such data exists.

## Remaining gates

- GitHub Actions deployment succeeded after correcting the Tailscale subject to
  the owner/repository-ID format received from GitHub. The deployed `f4cc5cd` image
  was independently checked against merged `develop`; services and offline models
  passed. GitHub environment settings were not independently read.
- Public trusted IP HTTPS and certificate renewal are now configured; see the
  [HTTPS preview](https-preview.md). Public routing is limited to health and the
  reserved MCP path. No Tailscale Funnel/Serve endpoint is enabled.
- External alert delivery was explicitly deferred by the user on 2026-11-05.
- PR 31 live ChatGPT connection and PRs 32–37 remain deferred. The measured
  concurrent-image memory pressure, embedding backpressure and grown-corpus recent
  query issues remain public-release blockers; hosting does not fix them.
- Confirm the off-server recovery copy of the backup passphrase and agree retention.
  Backup objects are currently retained without automatic deletion.
- Tailnet node expiry remains 2027-05-02. Health failures are recorded by systemd;
  external notifications have not been configured.
