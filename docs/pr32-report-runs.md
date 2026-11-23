# PR 32 report runs and automatic waiting

Branch `codex/pr-32-report-runs`; base and target `develop` after merged PR 31J
acceptance (`1ac9983`). Merged PR 32 (`f7e5692`) was deployed on 2026-11-21.
The single-prompt ChatGPT gate passed for the observed Bangkok run below; this
does not establish that every ChatGPT session will keep a five-minute turn open.

## Contract and storage

Three new tools use the strict `research-report-v1` output schema:

| Tool | Behavior |
| --- | --- |
| `request_scam_report(location_id, follow_run_id?, watch_seconds=300)` | Start/join shared research, or resume watching the specified existing run without queuing anything. Return a durable run ID and a separate bounded watch. |
| `get_scam_report(run_id, limit=5, offset=0)` | Read that run's retained public membership, reapplying current eligibility. Never starts work. |
| `wait_for_scam_report(run_id, watch_id, after_revision, wait_seconds=3, limit=5, offset=0)` | Wait for that page to change or the root to terminate. Never invokes a provider or changes research allowances. |

Migration **0022** adds `research_runs` and `report_watches`, a current-run pointer
on location state, and root membership on phase jobs. It preserves existing lease
fencing, outbox jobs, cursors, paid reservations, evidence and legacy status constraints.
New initial/refresh/scheduled location work creates a root; enrichment/continuation
shares it. Retries remain queued/nonterminal until exhausted. Public progress uses
durable ownership and lease expiry, not just a reservation token or last worker label.

The report separates latest run status, retained coverage and source freshness.
`partial` never becomes stale just because work stopped. Older complete findings can
remain available during a failed refresh. Completion means the declared bounded plan,
not exhaustive destination coverage. Known root errors survive phase transitions;
an empty incomplete result never becomes a completed negative. A never-researched
place has no report run until requested; an unknown run ID returns `not_found`.

Run membership includes a bounded baseline and that root's new candidates, at most
1,000 IDs. New findings take priority at the cap; pagination uses at most 20 cards
and offset 1,000. The response exposes the membership cap/truncation qualification.
A superseded run never silently follows a new root. Reads project current public
eligibility, so a historical view cannot preserve access to a withdrawn allegation.
Targeted hypothesis research remains an independent legacy workflow: it does not
silently become part of a destination report root or certify its completeness.

Revisions are opaque hashes of the public **page**, including safe progress and
metrics. Preserve limit/offset when waiting on a revision. Poll timestamps, watch
counts and `metrics.as_of` do not cause revisions by themselves. Eligibility/metrics
changes can change a revision even on a terminal historical run.

## Source times and compatibility

A successful ingestion checkpoints `fetched_at`, including unchanged-source fetches.
Only retained/scored claim application records source checks, keyed by pattern and
document. Reads qualify them against **currently public supporting evidence** before
projecting `source_checked_at`. Withdrawing a recently checked source cannot keep
older evidence artificially fresh. There are at most 64 retained checks per pattern
and 10,000 recorded-document candidates per freshness read; missing/truncated check
history stays unknown. This check time is not an occurrence date or a truth score.

Pattern freshness uses its latest qualified supporting-source check. Overall freshness
uses the oldest such pattern check; any missing pattern check makes it unknown unless
a known expiry already proves stale data. A genuinely completed negative can use its
actual root-completion time. `last_result_update_at` records in-run additions or
source-check changes; reads and heartbeats never advance it. Read-time publication
changes affect the revision without inventing historical result timestamps.

Only a completed, eligible fresh report gets completed-result cache admission.
Partial/failed attempts keep existing cooldowns; a completed plan whose retained
evidence is still unknown/stale also keeps the existing retry cooldown. It cannot
turn every subsequent read into paid work. Comparator budget denial is classified
as a resource stop, rather than a provider outage.

Legacy `intelligence-v1` REST and seven existing tools retain their envelope shapes
and enum values. Their coarse `stale` semantics remain a compatibility projection;
new report tools and PR 33 must use the corrected contract. Report cards omit the
legacy per-pattern research label to avoid confusing it with destination-run progress.
MCP now discovers ten tools with typed schemas for the three new report operations.

Legacy backfill adopts one conservative latest-run record and only the demonstrably
owned active job; it does not fabricate historical phase membership or timestamps.
An old initial-phase success does not prove root completion. Old source observation
times remain unknown until a qualified new fetch/application occurs. Backfill recursion
is bounded against invalid/cyclic geography. Downgrade drops new report/watch metadata
but retains legacy jobs, state, evidence and cumulative research allowances.

## Watch bounds

- Three-second change wait by default, five seconds maximum, below the existing
  12-second MCP tool timeout. Read/transport overhead is separate from the wait interval.
- Five-minute watch by default, ten-minute hard maximum from server request admission.
  Each durable watch admits at most 60 waits, with atomic counter updates.
- At most eight active waits per API process. Capacity returns available data and
  a retry hint; it does not occupy a worker or start another root.
- Watch admission changes only operational watch counters. All report projections use
  short read-only transactions; no connection/transaction remains open while sleeping.
- Cancellation releases wait capacity without cancelling shared research. Reconnect
  uses the same run/watch IDs. Explicit `follow_run_id` creates a new watch without
  buying research. Expired watches are retained at least seven days; roots are retained.

No new provider, credential, service, research budget increase, UI, notification or
moderation bypass is introduced. The active progress/report view is PR 33.

## Validation and rollout

Tests cover concurrent root sharing, phase membership, completion versus enrichment,
typed MCP discovery/round-trips, an automatic local wait/report/evidence loop, fresh
partial versus expired data, failed-refresh age, held evidence, source withdrawal,
negative cache reuse, stable revisions, cancellation with zero checked-out connections,
watch limits/resume/capacity, conservative migration/backfill/rollback and root fencing.
The fresh-negative legacy REST fixture now explicitly represents a genuinely completed
canonical run; initial completion alone is no longer sufficient proof.

Validation: **982 passed, 10 skipped**; Ruff lint/format and whitespace checks passed.
Migration 0022 matches ORM metadata (`alembic check`), its downgrade/backfill path is
covered in an isolated database schema, and the production image built. Frozen quality
replay retained prior case outcomes with 26 cold calls / 12,413 recorded tokens and
zero warm calls; no new paid provider capture or production report was used.

Before production, review/merge, stop old API/worker processes and apply 0022 using the
existing deployment path, then start matching images. `alembic check` must report no
model drift. The non-writing MCP smoke check verifies ten tools and missing-run/watch
errors without queuing research. Keep a database backup; image rollback can retain
the additive schema. A deliberate downgrade discards new report/watch addresses, so
old views must reconnect rather than receiving fabricated continuation history.

After deployment refresh the ChatGPT tool connection and use one natural prompt:

> Use Travel Scam Intelligence to check scams in Bangkok, Thailand. I am visiting
> next month. What should I keep in mind? Give me a sourced report. Do not submit a report.

Verify that it selects the request/wait/report flow, automatically follows the same
run/watch within bounds, fetches evidence and delivers a consolidated sourced report
without a manual follow-up. Check partial versus freshness, actual city/POI scopes,
unknown dates, candidate disclaimers and evidence-record wording. Record acknowledgement,
wait/terminal latency, calls, duplicate jobs, reservations and VPS connection/memory
behavior. If the host ends early, record that failure; the local loop and PR 31J's older
read test do not pass this new conversational gate. A deterministic five-minute UI
watch and in-place report/narrative capability check remain PR 33.

## Production acceptance — 2026-11-21

The encrypted offsite backup succeeded before activation. Matching API/worker
images at `f7e5692bc90a74ba9a4eddbaa132c1f25b8e91ab` passed readiness;
migration 0022 passed `alembic check`. The public MCP smoke discovered ten tools
and passed non-writing validation without starting research.

The user refreshed the ChatGPT connection and ran the single prompt above without
a manual follow-up. Backend run `7507399a-4a00-44df-a303-1a5119c68578` started at
15:14:02 UTC and terminated at 15:15:17 UTC (about 76 seconds from admission).
One observed watch performed six waits. Its three linked jobs (refresh and two
continuations) each succeeded on their first attempt; these are phases of the same
root, not three independent investigations. The terminal contract validated as
`partial`, with `resource_limit` and `source_failures`; retained coverage was
`partial`, source freshness `unknown`, and no successful-completion time was invented.
Allowances remained fetches 20/20, queries 6/6, documents 7/10 and LLM calls 23/24.

ChatGPT delivered a consolidated answer containing the two retained candidates,
their source, limited confidence and missing dates. It kept Bangkok taxi attribution
separate from the Grand Palace bird-feeding attribution, described terminal partial
research rather than calling it stale, and stated that no incident report was submitted.
No new public pattern yield is claimed for this run. The user supplied the final
answer, not a raw tool transcript or host duration: backend watch counts verify
automatic waiting, but acknowledgement latency and individual host calls are unmeasured.

Presentation caveats: ChatGPT also added external travel guidance and many illustrative
images. Those sources were not audited in this acceptance and are not new app evidence,
independent corroboration in the graph, or improved confidence scores. Image discipline
remains imperfect. This gate covers the observed conversational flow, not full research
coverage, arbitrary destinations, five-minute host endurance or PR 33's progress UI.

The post-run sample showed API 339.2 MiB and worker 207.9 MiB, low sampled CPU,
and zero idle-in-transaction database sessions. These are point samples, not load
peaks or whole-host sizing evidence. Sanitized backend evidence is in
`benchmarks/data/32/`. Browser automation was denied by automatic approval review
(Google Chrome access was not approved); the user performed the actual ChatGPT UI test.
