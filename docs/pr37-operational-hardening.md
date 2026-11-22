# PR 37 — Operational hardening

Branch `codex/pr-37-operational-hardening` starts from merged PR 36 on `develop`
(`7e59ed9`), 2026-11-22. Scope is anonymous ingress protection, private operational
visibility, dependency auditing and bounded local performance measurement. This PR
does not deploy a release, close the deferred PR 33 explorer host gate or configure
the postponed external alert destination.

## Anonymous service allowances

These are shared service allowances, not per-person limits. ChatGPT and Funnel do
not provide a trustworthy end-user identity. Forwarded IP headers, request IDs,
report keys and arbitrary client identifiers cannot create new allowance buckets.
An abusive client can still consume the shared allowance; this is an explicit
availability tradeoff of an anonymous MVP without accounts or CAPTCHA.

| Control | Default | Scope |
| --- | --- | --- |
| Ordinary/MCP body | 65,536 bytes | Enforced before parsing, including chunked bodies |
| Body read deadline | 10 seconds | Ordinary buffering; media streaming only |
| Private JPEG/PNG upload | Existing 5 MiB | Incremental reader plus early declared-size rejection |
| HTTP requests | 240/minute | Per API process, fixed calendar-minute windows |
| Concurrent HTTP requests | 16 | Per API process; health/readiness bypass |
| Concurrent media uploads | 1 | Per API process; bounds decoder pressure |
| New incident reports | 5/10 minutes, 40/day | Shared atomic Redis counters |
| Explicit new research | 6/10 minutes, 100/day | Shared atomic Redis counters |
| New report watches | 120/10 minutes, 1,000/day | Shared atomic Redis counters |

The HTTP allowance covers reads, polls and rejected requests. Ten-minute/day
allowances cover new work only. Existing accepted report receipts bypass admission,
including while Redis is unavailable. Existing/shared active research and report
get/wait calls do not consume new-research credit. Opening a new watch creates a
new record and consumes watch credit; reopening after a lost watch response can
consume another watch credit. The existing 60-wait/600-second bounds still apply.
HTTP capacity rejection is 503; rate rejection is 429 with a retry hint. MCP uses
its existing safe tool-error envelope with `rate_limited` or `unavailable`.

Redis checks both quota windows atomically before charging either. It stores only
hashed receipt identifiers and bounded counts with expiring keys. Reusing an
admitted receipt does not charge it twice within a window. Windows are fixed UTC
epoch intervals, so bursts spanning a boundary may admit twice one window's limit.
New admissions fail closed within one second if Redis is unavailable. Failed
transactions can conservatively consume credit until expiry; admission is not a
payment ledger. Per-root provider budgets, worker leases, retries and scheduler
resource reservations remain authoritative and unchanged.

Quota/concurrency enforcement is production-only. Body size, encoding and timeout
checks apply in local/test too. `APP_OPERATIONS` accepts a validated JSON object;
omitted fields use defaults. For example:

```dotenv
APP_OPERATIONS='{"http_per_minute":240,"reports_per_day":40,"research_per_day":100}'
```

Deployment Compose accepts the same JSON as nonsecret `OPERATIONS_POLICY` in its
host environment file. `enabled=false` disables quota/concurrency enforcement,
not body safety. One API process is the intended production topology; increasing
replicas multiplies process-local HTTP limits, while new-work quotas stay shared.
Anonymous quotas reset on loss of Redis state. This is a cost/abuse guard, not a
distributed denial-of-service defense or a guaranteed total provider spend cap.

## Existing safety boundaries and logs

Source fetches retain HTTP(S)-only canonicalization, public-only DNS validation,
pinned resolved addresses with TLS hostname checking, validation of every redirect,
HTTPS downgrade rejection and bounded bytes/deadlines. Tests include mixed public/
private DNS and IPv4-mapped loopback/metadata addresses. No fetch implementation
was replaced. User-provided source links are not fetched by report intake.

Existing PII redaction, named/sensitive allegation holds, private raw storage,
media normalization and moderation eligibility remain in effect. Ordinary
low-confidence candidates remain visible with their existing disclaimers.
Accepted reports are allegations, not verification.

Application logging uses fixed event names and validated metadata. It never
formats arbitrary message arguments or exception strings. HTTP path logs use route
templates; unmatched paths become `/<unmatched>`. Query strings, request/response
bodies, authorization headers and provider prompts are excluded. Sensitive SDK,
HTTP, worker-framework and storage diagnostic logs are suppressed; operator
diagnosis uses numeric telemetry and persisted job state. Do not enable raw SDK
debug/access logs or put credentials into a caller-supplied request ID.

## Operator metrics

Use the private tailnet/SSH-forwarded admin console, choose **Operations metrics**
and refresh when needed. The endpoint is `GET /v1/admin/operations`, protected by
the same bearer admin token, no-store headers and existing private network boundary.
Public MCP tools and Caddy/Funnel route allowlists are unchanged.

The snapshot separates:

- Redis counters for the current UTC calendar day: HTTP/tool latency, 5xx/tool
  errors, HTTP 4xx rejections, provider search requests/results, fetch calls/errors,
  Sarvam observed requests/tokens/unknown usage, embedding calls/text count, worker
  dispatch attempt/retry timing and scheduler invocation timing.
- Current persisted totals grouped by job/document/claim/hypothesis state, report
  processing/moderation state, resolver decision/review flag, evidence count,
  expired running leases and persisted scheduler cycles. These are all-time state
  counts, not daily event counts. Today's scheduler resource reservations are
  shown separately and are reservations rather than billed usage.
- Counts of claims, hypotheses, reports and jobs created since UTC midnight,
  separately labeled from current state totals.
- End-to-end and queue-wait means/maxima over valid timestamps in the newest 200
  research runs. The sample size and number of usable timings are explicit; no
  missing timestamp is converted into zero latency. Running/partial/failed states
  and coverage are separate. These measurements include queueing and background
  research; the HTTP request time does not measure the whole research run.

Counters have fixed labels and a two-day TTL. Each duration belongs to exactly one
bin (`le_100`, `le_500`, `le_2000`, `le_10000`, `gt_10000`); these are disjoint bins,
not cumulative Prometheus buckets. `duration_ms/count` is a mean. Use
`1-errors/count` for observed fetch success, with the denominator interpreted as
calls. Provider wrappers preserve return values, model metadata and cancellations.
Missing Sarvam token usage increments `usage_unknown`, not a fictitious zero-cost
bill. Worker dispatch counters include no-op/retry invocations; durable job status
is the source for final success/failure. Resolver SAME decisions and evidence counts
do not prove independent corroboration. No new corroboration-quality score is
invented; evidence review and PR 36's labeled quality harness retain that role.

Metrics writes fail open with a 150 ms bound; the snapshot has a 500 ms Redis bound
and reports unavailable counters as `values:null`. A Redis reset, timeout or partial
pipeline failure can lose observations; this is operational telemetry, not billing,
auditing or a durable historical monitoring system. External alerting remains
deferred as requested. Existing service/backup/HTTPS checks remain available.

## Dependency gate

CI exports all locked runtime dependencies and runs pinned `pip-audit==2.10.0`
against the current advisory database. Audit/database failures fail the check;
there are no ignored advisory IDs. Weekly Dependabot updates target `develop`
for uv dependencies and GitHub Actions.

The initial 2026-11-22 audit flagged three packages. The tested lock now uses Pillow
13.0.0, certifi 2026.10.18 and OpenTelemetry API 1.45.2. Primary advisory examples:
[Pillow JPEG decoder](https://github.com/advisories/GHSA-8g5g-8vmr-9782),
[certifi trust store](https://github.com/advisories/GHSA-6q48-6c7w-9gw2),
[OpenTelemetry trace parsing](https://github.com/advisories/GHSA-mf5v-w3w3-f6cg).
The patched host-applicable runtime audit checked 89 packages with no skips or
known advisories. An additional audit of every registry package in the lockfile
checked 96 packages (including platform variants/development tools), also with
no skips or known advisories. See [the audit summary](../benchmarks/data/37/dependency-audit.json).
The resolver's registry requests repeatedly timed out, so the three package entries
were refreshed from official PyPI version JSON/artifact hashes after checking that
their dependency edges were unchanged. `uv lock --check --offline` and
`uv sync --locked` both passed; the latter installed all three patched versions.

The audit is advisory-database coverage, not proof that every reported decoder
path is reachable or that every dependency is safe. The lockfile and tested
patched versions must pass audit before review.

## Measured performance

```sh
docker compose up -d --wait postgres redis
uv run --locked python -m benchmarks.operations --output .tools/pr37-load.json
```

The harness rejects nonlocal databases through the existing isolated-schema helper,
uses 50 synthetic public candidates/250 evidence records, makes no provider calls
or object writes, checks that all candidates are actually visible, and removes its
generated schema and Redis telemetry key. Requests alternate public location lists,
pattern detail and evidence; research is explicitly disabled. The committed
[measurement](../benchmarks/data/37/local-read-load.json) is Windows/Python 3.12,
in-process HTTP, local Docker PostgreSQL pool size 5 and Redis telemetry enabled.

| Concurrency | Requests / HTTP 200 | Requests/sec | p50 | p95 | p99 |
| --- | --- | --- | --- | --- | --- |
| 1 | 60 / 60 | 14.65 | 22 ms | 167 ms | 186 ms |
| 4 | 60 / 60 | 35.28 | 43 ms | 271 ms | 300 ms |
| 8 | 60 / 60 | 31.95 | 122 ms | 535 ms | 598 ms |

These timings preceded the dependency-only security updates. Each mix executed
960 SQL statements (16/request on average). Eight concurrent
reads did not improve throughput over four and raised latency. This does not
justify increasing production concurrency. The existing production pool size 2,
single worker, single LLM concurrency and embedding batch limits are retained.
At the planned 100 requests/day, this small warm-read test does not justify a
result cache, extra indexes or vector tuning. Public eligibility must continue to
reflect holds/removals immediately. Larger corpora and production latency may
justify a separately measured query optimization later.

These measurements exclude network/TLS, real model inference, source fetches,
worker coexistence and the Droplet's CPU/memory. They are not a production
capacity claim or evidence that five-minute research will finish sooner.

## Review and rollback

Local validation on 2026-11-22: the patched full suite passed **1,062 tests with
10 skipped**; subsequent focused checks passed for final lifecycle/context guards,
private operations projections and slow media body handling (22, 6 and 18 tests
respectively). All **25 Node UI tests** passed, including operations loading and
lock/clear. PR 36's 49-case replay passed with no baseline regression. Ruff and
Compose validation passed, including an actual JSON policy override.

The canonical Linux Docker build reached locked dependency installation but timed
out downloading PyPI wheels. The host-network retry also timed out, on unchanged
`huggingface-hub==1.33.0` (the first attempt failed on `filelock==4.2.4`). A successful
Linux image build remains an open CI/review requirement; the host test/audit results do
not imply a successfully built or deployed production image.

Security tests cover envelope rejection, chunked limits, deadlines, forwarding-header
spoofing, capacity release on cancellation, atomic cross-instance quotas, bounded
Redis storage, outage behavior, real report-receipt replay, shared research/polling,
log redaction, SSRF and private metrics authentication. Run the complete pytest/UI
suites, Ruff, Compose validation, PR 36 replay and dependency audit before merge.
Production deployment and live verification require a subsequent user request.

No migration is needed. Roll back to the previous application image if necessary;
the optional nonsecret policy can be removed. Expiring Redis quota/metrics keys are
ignored by earlier code. Do not roll back dependency security fixes casually.
The deferred explorer rendering check, external alert destination and final
release/recovery acceptance remain open.
