# Separate research progress, freshness and stop reasons

Decision date: 2026-11-08. Planned as part of PR 32's research/report contracts;
PR 33 renders the states. No runtime or production change is made by this document.

## Problem

`app/on_demand.py::_finish` currently assigns `stale` to an incomplete terminal run
once an initial attempt has occurred. Separately, the public service derives stale
from an expired `fresh_until`. These are different conditions sharing one status.
`initial_completed_at` means an initial attempt ended, not that research succeeded.

PR 31F demonstrated the problem: newly acquired public findings in Bangkok and
Suva were labeled stale immediately, even with `fresh_until=null`. ChatGPT accurately
repeated a misleading backend label. Changing only the UI wording will leave API,
scheduler and cached-read behavior inconsistent.

## Canonical model

Expose these dimensions separately in the new versioned report contract:

| Dimension | Values / meaning |
| --- | --- |
| Latest research run | `never_researched`, `queued`, `researching`, `completed`, `partial`, `failed`. Describes the root across phase jobs, not one worker attempt. |
| Retained report coverage | `complete`, `partial`, `unknown`. A bounded root may complete its declared plan; this never means exhaustive real-world coverage. |
| Retained report freshness | `fresh`, `stale`, `unknown`. A recheck-policy age signal for retained public data, independent of run completion. |
| Result outcome | Existing `findings`, `nothing_found`, `research_incomplete` semantics. Findings may coexist with partial research. |
| Stop reasons | Structured public reason codes, populated when the root actually terminates. Multiple reasons may apply. |

A root is `partial` when it retained valid progress but did not finish its allowed
plan. It is `failed` when it could not perform the research successfully. The public
report can still retain older findings when the latest attempt fails; a new failure
must not erase their coverage, source lineage, age or publication restrictions.

Distinguish `latest_run` from `retained_report` in the envelope. A refresh can be
running or failed while previously collected information is still fresh, or while
it has genuinely expired. `stale` is not a run status in the new model.

Queued means durable work is waiting for execution/retry; researching means a
current fenced worker is executing. The old reservation token alone cannot
distinguish these. Derive the transition from durable job/root ownership. Completion
of an initial phase does not mark the root completed while enrichment is pending.
A worker cancellation/retry is not root failure, and closing a UI watcher does not
cancel shared research.

Proposed sanitized stop codes: `time_limit`, `resource_limit`, `source_failures`,
`provider_unavailable`, `processing_incomplete`, `review_pending`, `retry_exhausted`.
Persist the real causes at the point they occur, then project them through a public
allowlist. Do not infer a precise cause from a generic historical `partial` status.
Do not expose traceback text, provider responses, internal cursor or lease tokens.
Review-pending work is terminal for this public watch; do not poll indefinitely for
admin approval or publish held allegations to reach completion.

## Timestamp and freshness policy

Track `last_attempt_at`/`last_checked_at`, `last_result_update_at`, `last_completed_at`
and `fresh_until` with explicit meanings:

- A finished attempt updates the check time, including failed checks; it is not
  proof that retained results were refreshed.
- Newly retained/revalidated public data updates its report observation time.
  Polling, job heartbeats, a denied fetch or a failed refresh cannot rejuvenate
  old evidence. Individual claims keep their own retrieval/publication/occurrence
  dates; new checking never fabricates a new incident date.
- `last_completed_at` is set only for a genuinely completed root. Keep historical
  `last_success_at` semantics separate until a migration can prove equivalence;
  old initial-phase success does not prove root completion.
- Compute freshness from a trustworthy report observation/revalidation time and
  the existing recheck policy. No such time means `unknown`, not `stale`. Only
  expiry of a real freshness window produces `stale`.

Recent partial findings may be fresh **and** incomplete. Display “recently checked”
with that coverage qualification, rather than implying full destination coverage.
A source fetched today may describe an old incident; report freshness is not
occurrence recency, source correctness, confidence or verification.

Keep cache reuse and admission explicit: only a completed eligible fresh report
satisfies the existing completed-result fast path. A fresh partial run still obeys
the existing incomplete-run cooldown and budgets. Stop using the overloaded status
string to decide whether new work is allowed. Poll/read operations must never start
another run or move any timestamp/expiry forward.

## User-facing behavior

| Situation | Expected presentation |
| --- | --- |
| Newly finished partial run, recent retained evidence | “Partial research — recently checked; resource limit reached” when that reason is known. Show findings, actual scope and remaining gaps. |
| Incomplete run with historical/unknown timing | “Partial research — last checked time unavailable.” Do not invent freshness or an expiry. |
| Completed research, before recheck expiry | “Research completed — recently checked.” Preserve bounded-source limitations. |
| Completed report past its recheck window | “Research needs refreshing — last checked [date].” Retain findings and actual dates. |
| Refresh executing with an older report | “Refreshing research,” plus the separate age/coverage of the retained report. |
| Refresh fails but old findings remain | “Latest refresh failed,” plus retained results and their unchanged age/coverage. |
| Completed negative, no eligible findings | “No findings in the sources checked.” |
| Partial/failed research, no findings | “Research incomplete — no findings available yet,” with terminal/progress state and known reason. Never `nothing_found`. |

These are display templates, not claims that every current partial run stopped for
the same reason. Confidence/verification labels on individual patterns stay separate.

## Migration and compatibility

Implement a coordinated PR 32 change, not an isolated text substitution:

1. Specify the new report-state DTO and root transition table alongside stable run
   identity. Version the new envelope (for example `research-report-v1`); do not add
   unknown enum values or fields silently to strict `intelligence-v1` clients.
2. Add durable outcome/reason/timestamp metadata and update the location-state check
   constraints if canonical statuses are stored there. Update the lease constraint
   deliberately for queued reservations and running ownership. Keep existing fencing,
   active-job uniqueness and job-outbox semantics intact.
3. Update all writers/readers together: location worker finish/retry/take paths,
   initial/enrichment/continuation aggregation, scheduled location and targeted
   hypothesis paths, public intelligence projections, request admission and cooldowns.
   Read-only contract adapters must not rewrite research state as a side effect.
4. Backfill conservatively. Proven terminal complete records map to completed;
   proven running/queued work follows job ownership. Legacy stale with explicit
   incomplete progress maps to partial coverage/run outcome; expired complete
   research maps to completed plus stale freshness. Missing/ambiguous history stays
   unknown where applicable. Never use row `updated_at`, migration time or
   `initial_completed_at` as a fabricated successful check/result timestamp. Existing
   partial states without reliable terminal reasons may say “reason unavailable.”
5. Keep legacy read envelopes valid via an explicit compatibility projection while
   new report tools and UI use the corrected DTO. Mark old stale semantics as legacy
   in tool/contract documentation; do not instruct new ChatGPT flows to interpret
   legacy stale as expired evidence. A later REST `/v2` rollout may be separate;
   preserve `/v1` shape rather than quietly changing its contract.
6. Stop old workers during migration and deploy compatible API/worker versions
   together. Provide reversible schema/legacy-status mappings without discarding
   retained evidence, budgets or attempt history. A rollback must not reset research
   allowances or retry work indefinitely.

Both the scheduler and on-demand path need tests proving that corrected labels do
not change resource admission accidentally. Reporting “recently checked, partial”
must not grant a completed-result cache exemption or cause a refresh on every read.

## Delivery and acceptance

PR 32 implements and reviews the contracts, state transitions, migration/backfill,
compatibility and scheduling behavior before wiring them into the automatic
[report/wait flow](automatic-research-reports.md). PR 33 uses the same DTO for the
progress view, final report and narrative guidance; it must not infer freshness from
an old status string. The existing PR 31F quality gate remains unchanged.

Acceptance checks:

- A fresh partial root is not stale merely because its coverage is incomplete.
- A real expired window is stale even if the last root completed successfully.
- Failed refreshes preserve prior findings and do not extend their freshness.
- Missing legacy timestamps/reasons remain unknown through migration and rendering.
- Initial completion with a successor and retry/cancellation cases stay nonterminal.
- Terminal resource/provider/source/review stops show the correct partial/failed
  outcome; zero findings do not become a completed negative.
- Time-boundary tests use an injected clock; timezone formatting does not change UTC
  freshness calculations or turn incident dates into check dates.
- Concurrent request, cooldown, scheduler selection, lease fencing and cumulative
  budget tests retain their existing guarantees.
- Existing REST/MCP consumers still validate their legacy envelopes; new tools/UI
  agree on root progress, retained coverage, freshness and causes.
- Real ChatGPT displays a newly retained, budget-limited result as partial/recently
  checked, while an actually expired fixture/report is labeled as needing refresh.
  Reuse the Bangkok/Suva facts; do not relabel their old evidence as newly acquired
  merely by deploying this change.
