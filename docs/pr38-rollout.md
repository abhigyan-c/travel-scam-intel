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
- Only seed locations exist in this new database; no fabricated scam evidence or
  test reports were inserted into production. The restore drill does not prove
  recovery of future user media or provenance; verify those after such data exists.

## Remaining gates

- First real GitHub Actions OIDC/ACL connection. The user reports all environment
  variables/secrets saved; GitHub configuration has not been independently read.
  This new workflow must exist on the default branch before `workflow_dispatch`
  becomes available. Use the production environment's branch restrictions.
- Public trusted HTTPS, certificate renewal and alert delivery are pending.
  No Funnel/Serve endpoint or public HTTP listener is enabled by this rollout.
- PR 31 live ChatGPT connection and PRs 32–37 remain deferred. The measured
  concurrent-image memory pressure, embedding backpressure and grown-corpus recent
  query issues remain public-release blockers; hosting does not fix them.
- Confirm the off-server recovery copy of the backup passphrase and agree retention.
  Backup objects are currently retained without automatic deletion.
- Tailnet node expiry remains 2027-05-02. Health failures are recorded by systemd;
  external notifications have not been configured.
