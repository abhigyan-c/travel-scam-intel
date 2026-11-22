# Travel Scam Intelligence: administrator guide

Version 0.1.0 · Updated 22 November 2026

This guide covers private administration, moderation, deployment and recovery.
It does not authorize changes to production. Historical PR documents contain
point-in-time evidence; use this guide and the release checklist for current gates.

## Architecture and trust boundaries

One DigitalOcean Ubuntu Droplet runs the API, one worker, PostgreSQL/pgvector and
Redis. Caddy exposes a filtered listener through the approved Tailscale Funnel
hostname. There is no load balancer or automatic failover. The design targets
approximately 100 requests/day, not public-directory scale without monitoring.

Serper acquires English search results; Sarvam 105b performs semantic processing.
The pinned local BAAI/bge-small-en-v1.5 quantized ONNX embedding model produces
384-dimensional vectors for semantic retrieval/deduplication in pgvector. It is
not the language model. Models are preloaded; serving does not download them.

Production object storage is private R2 (`travel-scam-objects-prod`); encrypted
database backups use `travel-scam-backups-prod`. Bitwarden projects hold app and
backup secrets. GitHub Actions pushes a built immutable image over SSH through
Tailscale. Public routes must never expose REST, admin, readiness details, docs,
media, databases, Docker or the Caddy admin port.

The release adds exact read-only `/about`, `/support`, `/privacy` and `/terms`
routes plus the exact OpenAI domain challenge path. These are additional public
routes, not permission to proxy all of port 8000. Funnel targets **127.0.0.1:8080**,
the filtered listener, never the unfiltered application port.

## Accessing the admin dashboard

Connect your operator machine to the authorized tailnet and use the verified SSH
alias. Open a local-only forward:

```sh
ssh -N -L 127.0.0.1:8035:127.0.0.1:8000 travel-scam-prod
```

Open `http://127.0.0.1:8035/admin`. Retrieve `ADMIN_API_TOKEN` through the authorized
secret-manager workflow and enter it in the dashboard's password field. Do not put
it in a URL, terminal history, screenshot, chat or issue. The UI keeps it in memory,
uses bearer headers and does not persist it in local storage. Lock/close the view
when finished. Locking also clears private working data and retry context.

This is a shared-token administration model. Audit records do not establish
individual administrator identity and are not an external tamper-proof audit log.
Anyone with deployment access can execute privileged application code; protect
repository writers, production environment permissions and SSH credentials.

## Daily operational review

1. Check private `/ready`, API/worker health, failed systemd units, free disk and
   recent backup success. Public `/health` confirms liveness only.
2. Check the Operations dashboard: errors, queued/failed jobs, expired leases,
   provider latency/token usage and recent research completion/partial outcomes.
3. Review held reports, unresolved locations and resolution proposals. Low
   confidence by itself does not require withholding an otherwise eligible claim.
4. Inspect unexpected growth in requests, work admissions, R2 use or provider
   spend. Shared quotas are not a provider spending cap.
5. Review support/security requests without copying private content into issues.

Read-only host diagnostics:

```sh
cat /opt/travel-scam/active-release
docker ps --format 'table {{.Names}}\t{{.Status}}'
systemctl --failed --no-legend
systemctl list-timers 'travel-scam-*'
```

`/opt/travel-scam/current` is a directory symlink; `active-release` contains the
deployed SHA. Compare that SHA with the release under review. A merged dependency
fix is not installed until its image is deployed.

The hourly health timer records failures, but external alert delivery is deferred.
Configure and exercise an alert destination before unattended public operation.
The user previously deferred Healthchecks.io setup; do not claim it is enabled.

## Moderation and correction

Use the dashboard to inspect full context before acting. Read the original source,
extracted claim, actual location, dates, provenance and current version. Require a
specific reason and review the confirmation summary for each mutation.

| Area | Operational practice |
| --- | --- |
| Reports | Distinguish processing from review. Acceptance is not verification. Sensitive or unresolved holds cannot be bypassed merely by accepting a report. Reopen a rejected report before an allowed acceptance transition. |
| Hypotheses/patterns | Keep allegation wording and geographic scope. Candidate visibility does not imply verified incidents, frequency or current activity. |
| Resolution proposals | Inspect both sides and evidence before merge/split/move. The UI loads targets and enforces scope/version constraints. Do not merge unrelated locations just to improve yield. |
| Evidence | Preserve provenance and historical records. Two records from one article do not create two independent corroborations. |
| Documents | Re-normalization changes normalized content; it is not automatically a fresh semantic extraction run. |
| Locations | Scheduler priority ranges from -100 to 100; reset to 0 when a temporary priority ends. Priority affects eligible work and does not bypass freshness or budgets. |
| Emerging patterns | Acknowledging/dismissing a signal is an admin action, not verification or a confidence/publication change. |
| Jobs | Retry only supported failed ingestion/processing/report jobs. Do not manually reset research leases/cursors to force work. Held reports are not fixed by repeated retries. |

Merge/split/move operations preserve history and invalidate affected scoring or
embedding work; do not assume immediate recomputation. Existing action limits
bound evidence selection (100 for split/move and 1,000 for merge).

On HTTP 409, reload and review the changed record rather than resending a stale
decision. If an action may have succeeded but the response was lost, preserve the
same action ID and exact confirmed payload for the supported retry. Do not create
a second operation. If the view was closed, reconcile state and audit history first.

## Reading metrics correctly

Private `/v1/admin/operations` exposes fixed-label Redis counters for the UTC day
and durable database summaries. Redis counters expire after two days and can be
lost on reset; missing telemetry is unknown, not proof of zero usage. LLM usage
may be unknown when a provider omits it. It is not a billing ledger.

HTTP 4xx and 5xx, provider activity, worker attempts/retries and scheduler cycles
have different meanings. The database's status groups are lifetime counts;
`created_today` uses UTC. Queue/end-to-end latency samples cover the latest 200
valid research runs, not every historical job. Latency buckets are disjoint, not
cumulative Prometheus histogram buckets. Expired leases need investigation.
Quality evaluation and source-independence/confidence metrics are separate from
availability metrics. See [PR 36](pr36-evaluation-harness.md).

## Capacity and abuse controls

PR 37 production defaults include 240 HTTP requests/minute per process, 16 active
requests and one active media request. Health/readiness probes bypass those limits.
JSON bodies are bounded at 65,536 bytes with a ten-second body deadline. Media has
a separate 5 MiB streaming bound and deadline. Caddy also imposes an outer limit.

Redis coordinates service-wide anonymous admissions:

| New work | Ten-minute limit | Daily limit |
| --- | ---: | ---: |
| Reports | 5 | 40 |
| Root research requests | 6 | 100 |
| Watches | 120 | 1,000 |

Reading existing work and reusing an active run do not consume new-work admission;
ordinary HTTP limits still apply. Existing idempotent report receipts remain
recoverable. New work fails closed if the shared admission store is unavailable.
429 means retry later; 503 can mean admission storage is unavailable. These limits
are global, not per person, and do not trust a supplied forwarded IP as identity.
Fixed windows permit boundary bursts. Redis resets lose quota history.

Tune `OPERATIONS_POLICY`/`APP_OPERATIONS` only with measured resource/provider
capacity and tests. Keep worker concurrency one and the small database pool until
measurements justify increases. Scheduler opt-in and budgets are separate controls.

## Secrets and rotation

Root-owned host configuration lives under `/etc/travel-scam` (directory 0700,
secret files 0600). Machine tokens are separate files. Runtime exports under `/run`
are mounted as files; they must not enter images, Git, logs or CI artifacts.
Use separate app/backup project and bucket credentials where practical. The current
bootstrap token and R2 pair can access both projects/buckets: this broader scope is
a known least-privilege limitation, not isolation between app and backup authority.

Never print `.env`, secret-manager output or expanded Compose secrets. Use
`docker compose ... config --quiet` for validation. Rotating a database secret in
Bitwarden alone does not change the existing PostgreSQL role password. Coordinate
database change, secret export, application activation and recovery verification.
Keep the old recoverable state until the new one is validated; never include secret
values in the audit record. See [deployment configuration](../deploy/README.md).

## Deploying a release

1. Merge reviewed preparation changes into `develop`, then review the
   `develop` → `main` release PR. Require CI, security evidence and release gates.
   Do not deploy a branch name without identifying its exact commit.
2. Use GitHub's manually dispatched **Deploy VPS preview** workflow on the approved
   ref. Production environment restrictions and its Tailscale OIDC identity must
   permit the chosen branch. A merge alone does not deploy anything.
3. The workflow builds and transfers an immutable image and activation bundle over
   SSH. Activation exports secrets, stops API/worker, migrates/seeds, starts services
   and checks readiness before recording the active SHA. Expect brief downtime.
4. Verify `active-release`, actual installed versions, readiness, MCP read flow,
   public allowlist/private denials, TLS, queues and backup creation. Validate any
   changed UI in actual ChatGPT; protocol tests alone cannot prove rendering.
5. For policy/Caddy changes, follow the [publishing runbook](publishing-readiness.md).
   Check whether host configuration needs a separate operator install; an app image
   alone does not update host Caddy or domain verification files.

Deployment failure does not undo database migrations. Rollback to a retained image
only after checking schema compatibility. Do not run automatic downgrade or
restore over production merely to make a rollout green. The first release must
replace the currently observed PR 35 image before public launch.

## Backup, restore and retention

The nightly timer invokes encrypted PostgreSQL backup to private R2. A successful
timer invocation is not enough: check upload success and backup age. The SQL dump
does not include application R2 objects or Redis contents. Keep the independent
backup passphrase recoverable off the VPS; a vault reference alone is not proof
that disaster recovery works when the host or account is lost.

Use `deploy/verify-backup.sh` on the configured host to retrieve the last successful
encrypted backup and run the isolated restore. For an already downloaded backup:

```sh
bash deploy/restore-drill.sh downloaded.dump.gpg protected-passphrase-file
```

The drill uses a disposable PostgreSQL 17/pgvector container without published
ports and removes its test resources. Never point it at production. Verify
migration level, representative counts, provenance relationships and retrieval of
referenced R2 objects. Redis is not the durable research record; inspect durable
jobs and use the supported dispatcher after recovery, avoiding duplicate writes.

Current reports, raw objects, source evidence, database history and encrypted
backups have no automatic expiry. The public policy discloses indefinite
retention. A future bounded-retention feature needs an explicit policy, deletion
implementation and recovery handling; do not silently add bucket lifecycle rules.

For a manual removal request, verify the affected record/source using minimal
information, restrict visibility where appropriate, and document which active
records/objects were actually removed or retained. Dashboard rejection/hiding is
not physical deletion. Record the necessary removal procedure separately so a
restore does not accidentally republish removed content. Do not promise immediate
erasure from immutable historical backups or a deletion SLA that does not exist.

## Incident response and release gates

If credentials or private content are exposed, restrict the affected public path
or workload, preserve redacted evidence, rotate compromised authority and verify
containment. Do not post sensitive logs in GitHub. Support/security contact:
**abhigyanchakraborty61@gmail.com**. Investigate provider bills and job history if
abuse is suspected; a passing test suite cannot rule out a breach.

Before public launch, complete the [release security assessment](release-0.1.0.md)
and [publishing checklist](publishing-readiness.md): patched production image,
alerts, recovery evidence, publicly served policies, verified publisher/domain,
real host UI acceptance and demo, platform review and explicit publication.
