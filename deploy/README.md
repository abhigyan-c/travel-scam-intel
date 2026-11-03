# PR 38 hosting foundation

Status (2026-11-03): the Droplet, private Tailscale administration and native service
preflight are ready; actual application deployment remains pending. See
[host readiness](../docs/host-readiness.md). This is
the early hosting slice of PR 38, not the final release or the deferred MCP adapter.

## Configuration

Use a plain Ubuntu 24.04 x86-64 host. Install Docker Engine/Compose, Python 3,
the official Bitwarden `bws` CLI, GnuPG and AWS CLI. Verify downloaded binaries
against the vendor's release checksums. Put protected root-owned configuration in
`/etc/travel-scam` (directory 0700; files 0600). Copy the two example env files;
they contain only nonsecret metadata. Store each machine token in its own file:
`app-machine-token` and `backup-machine-token`. Do not put tokens in CI or images.

In `prod-app`, use these exact secret keys:

- `DATABASE_URL`: `postgresql+psycopg://travel:URL_ENCODED_PASSWORD@postgres:5432/travel_scam_intel`
- `POSTGRES_PASSWORD`: the same database password, without URL encoding
- `REDIS_URL`: `redis://redis:6379/0` (private network only)
- `S3_ACCESS_KEY`, `S3_SECRET_KEY`: application-bucket credentials
- `SERPER_API_KEY`, `SARVAM_API_KEY`, `ADMIN_API_TOKEN`

In `prod-backup`: `S3_ACCESS_KEY`, `S3_SECRET_KEY` for the backup bucket, and
`BACKUP_PASSPHRASE`. Retain a recoverable copy of that passphrase off the VPS.

### Existing local credential names (2026-11-01)

The ignored local `.env` contains the following names. Live checks load values
programmatically without displaying them:

| Local name | Deployment use |
| --- | --- |
| `SERPER_API_KEY` | Same key name in `prod-app` |
| `SARVAM_API_KEY` | Same key name in `prod-app` |
| `BITWARDEN_MACHINE_ACCESS_TOKEN` | Bootstrap machine token; install into the appropriate host token file after verifying its project scope |
| `R2_ACCESS` | R2 S3 access-key ID; exporter maps it to `S3_ACCESS_KEY` |
| `R2_SECRET` | R2 S3 secret access key; exporter maps it to `S3_SECRET_KEY` |
| `R2_TOKEN` | Reserved for Cloudflare API management if needed; not consumed by application object access or the exporter |
| `R2_ENDPOINT` | Maps to host `S3_ENDPOINT` |
| `PROD_APP_UUID` | Maps to host `BWS_APP_PROJECT_ID` |
| `PROD_BACKUP_UUID` | Maps to host `BWS_BACKUP_PROJECT_ID` |

The R2 key pair has now passed live read/write and conditional-write checks against
both buckets. Vault projects may use either the
R2 aliases or canonical S3 names, but do not define both for the same credential:
the exporter rejects ambiguous duplicates. The local R2 names are deployment
references; local application/Compose storage settings continue to use `S3_*`.
The user confirmed that the supplied Bitwarden token accesses both projects; live
project reads confirmed this. This shared token can bootstrap both host token files.
It grants broader access than the originally proposed separate project tokens.
Likewise, the supplied R2 key pair accesses both buckets; separate per-bucket
credentials have not been supplied. Never copy bootstrap tokens into application
secret mounts. Use read/write machine access only for initialization and return to
read-only for ordinary deployments after seeding is complete.

### Live service setup

Run `python -m deploy.check_services` explicitly to check project access/required
secrets and upload/read/conditional-write/delete a unique synthetic object in each
bucket. It also attempts read-only public-domain configuration inspection with
`R2_TOKEN`. Provider output/values are captured, never printed. Temporary objects
are deleted and absence verified. If cleanup fails, only the synthetic object key
is reported for operator cleanup. This command is not run in CI.

`python -m deploy.seed_vault` prints a dry-run list of missing key names. With
`--apply`, it imports the supplied Serper/Sarvam/R2 credentials and generates the
database password, admin token and backup passphrase, then verifies vault readback.
It preserves existing values and can resume a partial run. A read-only machine
account can list projects but cannot initialize secrets; grant `Can read, write`
temporarily in both projects. A create request can return HTTP 404 in that situation.
Retain the backup passphrase in the vault for recovery; it is not printed or saved
to a plaintext local file by this script.

Live results (2026-11-01): both buckets passed upload/download, atomic conditional
write rejection, original-content preservation and temporary-object cleanup.
Unauthenticated S3 requests returned HTTP 400. That only establishes denial at the
S3 endpoint: inspection of `r2.dev` and custom-domain exposure returned HTTP 403
with the supplied management token, so confirm those public-access toggles disabled
in the Cloudflare dashboard before public release. Both Bitwarden projects were
initially empty; after the user enabled write access, initialization created eight
app secrets and three backup secrets and verified their values by vault readback.
An idempotency dry run then proposed no changes. Real app secrets were exported
inside a disposable network-isolated Linux container and loaded successfully by
application uid 10001; PostgreSQL password ownership was verified as uid 999.

The exporter validates required keys and duplicates before creating a new runtime
directory. Application secret files are owned by uid 10001; the PostgreSQL password
file is owned by the pinned image's postgres uid 999. Verify these IDs when upgrading
images. Never run
`compose config` without `--quiet` where secrets might have been injected by an
operator. Environment variables override secret files; the production profile
deliberately does not inject secret environment variables or mount `.env`.

Preload the pinned embedding model into `/opt/travel-scam/models` using the built
image before activation; grant
uid 10001 read access. Both application containers mount it read-only. Model
downloads do not happen during requests. Verify available RAM under real workload.

```sh
sudo install -d -o 10001 -g 10001 /opt/travel-scam/models
sudo docker run --rm --entrypoint python \
  -v /opt/travel-scam/models:/models travel-scam-intel:COMMIT_SHA \
  -c 'import asyncio; from app.embeddings import Embedder; asyncio.run(Embedder("/models").preload(download=True))'
```

## IP-based HTTPS

This is the earlier public-IP endpoint option. Administration now uses Tailscale;
choose the ChatGPT-facing public HTTPS/tunnel route before issuing an IP certificate.
No public endpoint is enabled by the host preflight.

Install Caddy as a host service and Certbot **5.4 or newer** with webroot support
for IP certificates. Use `Caddyfile.bootstrap` first: it serves only ACME challenges
on port 80 and returns 503 elsewhere. Create `/var/lib/travel-scam/acme` readable
by Caddy. Allow inbound TCP 80/443 and restrict SSH to the operator/deployment path.
Issue a staging certificate first, with a separate `--cert-name ip-staging`, and
do not install it into the public configuration. After that succeeds, request the
trusted production certificate:

```sh
sudo certbot certonly --webroot -w /var/lib/travel-scam/acme \
  --preferred-profile shortlived --ip-address VPS_PUBLIC_IP --cert-name vps-ip
```

Set `VPS_IP` in Caddy's systemd environment and install `Caddyfile`. Copy the issued
certificate/key into `/etc/caddy/certs` as root:caddy 0640 before starting that
configuration. Install `install-certificate.sh` as a Certbot deploy hook, then
enable the distribution's twice-daily renewal timer and run `certbot renew --dry-run`.
Validate the Caddy configuration and verify the certificate chain/IP SAN remotely.
Run `check-host.sh` hourly via the chosen monitoring service: it checks readiness,
backup age, disk usage and at least 48 hours of certificate validity. Configure an
actual alert destination before public use; the script alone sends no notifications.
All issuance, renewal and ChatGPT IP compatibility checks await the VPS.

Source: [Let's Encrypt's IP Certbot instructions](https://letsencrypt.org/2026/03/11/shorter-certs-certbot).

## Push deployment

The manually triggered `Deploy VPS preview` workflow builds the selected commit,
pushes a compressed Docker image and deployment files over SSH, and activates it.
No registry or repository credentials are needed on the VPS. Configure GitHub's
`production` environment with `VPS_HOST` and `VPS_USER` variables and secrets
`VPS_SSH_KEY` and `VPS_KNOWN_HOSTS`. Verify the SSH host key through the DigitalOcean
console before saving it; do not trust an unauthenticated `ssh-keyscan` result alone.

The dedicated deployment user needs write access under `/opt/travel-scam/releases`
and passwordless sudo for activation and Docker image loading. This is privileged
deployment access; restrict repository/workflow writers accordingly. Host bootstrap
and private CI access remain part of PR 38. The operator's private SSH and firewall
restriction have passed preflight. GitHub hosted runners need their own scoped
Tailscale identity before targeting the private host. No deployment runs on merge.

Activation exports fresh secrets into `/run`, starts private dependencies, stops
the old application, migrates/seeds, and waits for API/worker health. Failure exits
nonzero and leaves the release marker unchanged. It does not automatically undo
schema changes. Secrets on disk are not automatically updated database passwords;
coordinate credential rotation. Install `travel-scam.service` after the first
successful rollout to re-fetch runtime secrets after reboot. Cold boot requires
Bitwarden access. Old runtime export directories can be removed after confirming
no running containers mount them; no automatic pruning is performed yet.

## Backup and recovery

Install the backup service/timer after a successful first deployment. `backup.sh`
streams a consistent custom-format `pg_dump` through GPG encryption, then uploads
to the private R2 backup bucket. It records success only after upload succeeds and
never writes a plaintext dump. Backups currently use unique keys without automatic
deletion: set a retention policy only after agreeing the recovery window. Monitor
`last-backup-success`, disk use, and systemd failures; a timer alone is not monitoring.

Before live submissions, download one encrypted backup using the backup credential,
run `bash deploy/restore-drill.sh downloaded.dump.gpg protected-passphrase-file`.
It streams decryption into a disposable PostgreSQL 17/pgvector database with no
network or published ports, verifies SQL restore and removes the test container
and its anonymous volume on exit. Run this on an operator machine or replacement
host with sufficient disk/RAM. Check migrations, counts, provenance
and referenced R2 object retrieval before promoting a restored database. Never run
a restore drill over production. Keep application objects: a DB dump does not
include R2 objects or Redis queue contents. Re-dispatch durable database jobs after
recovery. Roll back application code by activating a retained SHA only after checking
schema compatibility; otherwise restore the verified backup and matching image.

## Remaining live gates

- Host provisioning, SSH restrictions, patching, firewall and reboot test.
- Caddy installation and trusted IP-certificate issuance/automatic renewal. API is
  loopback-only until then; this slice exposes no public application listener.
- Actual R2 immutable writes/private media checks and Bitwarden permissions test.
- Encrypted backup/isolated restore, deployment failure and rollback drills.
- Model preload, integration checks and memory profiling on the selected VPS.
- ChatGPT IP-URL acceptance and PR 31 MCP tools; later UI/security/evaluation gates.

See [architecture](../docs/minimal-mvp-deployment.md). Trusted IP HTTPS is still
the first route; a DNS hostname remains conditional on its compatibility test.

## Local validation (2026-11-01)

- Full existing suite with local infrastructure: 822 passed, 10 skipped; subsequent
  exporter regression check: 7 deployment tests passed (including one new case).
- Production Compose stack migrated/seeded a separate synthetic database and
  reached healthy API/worker status with read-only containers and file secrets.
  Confirmed worker concurrency 1 and database pool 2 with no overflow at runtime.
- Encrypted synthetic dump restored through `restore-drill.sh` into an isolated
  disposable database: migration 0021 and 31 public tables recovered.
- Compose configuration, Bash syntax, Caddy bootstrap validation and IP configuration
  parsing passed. Trusted certificate issuance and renewal were not attempted.
- Ruff lint and formatting passed. Actual R2/Bitwarden credentials were not used.
