# One request to a sourced travel-scam report

Decision date: 2026-11-08. User-authorized planning extension to PR 32/33.
Status: planned; no application behavior, resource limits or deployment changed.

## Product requirement

A traveler can ask once: “Use Travel Scam Intelligence to check scams in Rome,
Italy. I am visiting next month. What should I keep in mind?” The app resolves the
place, starts or joins bounded research, automatically follows progress, and
delivers the available sourced report. The normal flow must not require the
traveler to send “check again.” An already-fresh destination returns immediately.

The traveler may wait several minutes, with visible progress and available findings.
Five to ten minutes is an allowed waiting window, not a mandatory duration, a
promise of exhaustive coverage or permission to increase paid work indefinitely.
At a terminal partial/failure/budget stop, deliver the honest available report and
explain its limits. Never wait for moderation approval or a future scheduled refresh.

This updates the historical instruction that ChatGPT should always end its first
answer before deeper work completes. Individual MCP calls still stay short; research
continues in existing workers and completion is followed across short calls/UI updates.

## Capability boundary

Our backend owns durable jobs and report availability. ChatGPT owns turn continuation
and narrative generation. Tool descriptions can direct automatic follow-up calls,
but do not guarantee that a host keeps one turn running for five to ten minutes.
Do not advertise that guarantee based only on an MCP inspector or prompt instructions.

The supported UI should display progress and update the report automatically while
the view is active. Automatic plain-text narration after completion is a separate
host capability that requires a real ChatGPT test. If narration cannot be triggered
reliably, keep the complete report readable in the same view; record that limitation
rather than claiming the exact conversational experience passed. A one-click summary
action is a disclosed fallback, not a pass for automatic narration.

The [official widget reference](https://developers.openai.com/plugins/reference)
documents widget tool calls, state and follow-up messages. Their availability is
not proof that every target ChatGPT surface resumes the model identically. Recheck
the supported SDK/runtime and verify the actual account/surface at implementation.

[MCP Events](https://developers.openai.com/plugins/build/mcp-events) provides
subscriptions/webhook delivery on supported Work/Cloud/dot surfaces. It is not the
baseline for this anonymous custom MCP integration: authenticated subscription
ownership and account/surface support need separate design and validation. Do not
promise delivery after the user closes the view/chat, or add scheduled tasks,
notifications or a new webhook service as part of this plan.

## Architecture and contracts

Reuse the existing intelligence services, PostgreSQL leases/outbox, Redis/arq workers,
version-valid document/passage checkpoints and cumulative root budgets. No second
research pipeline, additional LLM provider, cloud service or OpenAI API key is needed.

Proposed public tool names are provisional until the PR 32 contract review:

| Operation | Responsibility |
| --- | --- |
| `request_scam_report(location_id)` | Return fresh public intelligence, or start/join one bounded root and return its public run ID and initial report/progress. Idempotent concurrent callers share work. |
| `wait_for_scam_report(run_id, after_revision, wait_seconds)` | Wait briefly for a public revision or terminal root state; return progress/report references and the next allowed check time. Never queue work or invoke a provider. |
| `get_scam_report(run_id)` | Read the bounded report with evidence references and actual location scopes. Retry/reconnect-safe; never restarts research. |

Keep existing immediate read/status tools compatible for callers that do not want
to wait. Resolve real geography before requesting a report; ambiguity still requires
clarification. A missing country root remains an actionable location error, not a
fake running job. Provision legitimate Italy/Rome location prerequisites before
using Rome as the live first-request acceptance case.

Persist a stable public run identity and root-to-job membership. The current mutable
location cursor alone is insufficient: a later refresh must not silently make an old
view follow a new root. Prefer extending existing durable job records with a root
manifest; use a small run table/migration if needed to retain identity, membership,
terminal outcome and report revision cleanly. Make that storage choice explicit in
PR 32. Public run IDs must never expose worker lease tokens, cursor contents, raw
objects, internal errors or private/held evidence. Public reads remain anonymous.
Every report/evidence read must reapply current public eligibility, including after
moderation changes. A saved terminal report is not permanent publication permission.

The response envelope needs a contract version, run/location IDs, public revision,
research status, `terminal`, structured stop reason, permitted next-check time,
watch expiry and a bounded public report or reference. Terminal means the **root**
has no eligible successor/retry under its current policy; one completed phase job
does not mean the root is done. A partial root may contain useful findings.

Keep progress states separate from claim/publication status. Internal stage
checkpoints are not a trustworthy percentage, ETA, verified finding or public report
revision. Advance public revisions only when the safely exposed progress/report changes.

## Waiting, polling and budgets

Proposed starting values, subject to the real-host and small-VPS checks:

| Control | Planned starting policy |
| --- | --- |
| One wait-tool call | 10 seconds by default, at most 15; shorten if measured host behavior requires it. This is our proposed cap, not an asserted ChatGPT timeout. |
| Automatic watch window | 5 minutes by default, hard ceiling 10 minutes, including queue/network time. |
| Model-driven follow-up | At most 60 waits and the absolute watch deadline, whichever comes first; honor next-check timing and stop promptly at terminal state. |
| Active UI polling | One in-flight request per view; start around 5 seconds, back off with jitter to 30 seconds on inactivity/transient errors; stop at terminal state or watch expiry. |
| Research work | Existing 31E root/phase/query/fetch/document/model/time caps unchanged. Polling never refunds, extends or resets them. |

Prefer change-aware waits over tight model polling. Release database sessions and
connections between checks; never hold a transaction, worker slot or model call while
sleeping. Use async waiting in the API, bounded active-wait capacity and retry hints
when that capacity is full. Redis notification may accelerate reads, but PostgreSQL
remains authoritative and missed notifications must not lose completion.

Multiple tabs/model/UI watchers must share the same research root without multiplying
provider calls. Pausing/closing one view stops its watcher, not shared research needed
by other callers. A poll failure must not trigger a new root. Reconnecting retrieves
the stored run/report and does not purchase research again. Expired/replaced/missing
runs and expired watches return explicit recoverable states, not successful emptiness.

On watch expiry, show the retained report and whether the root is still queued/running;
stop automatic calls and offer a resume action. This is the bounded exception to the
normal no-reprompt flow, not an unbounded promise to notify later. Retention/cleanup
must preserve a run at least throughout its watch and documented reconnect window.

A future optional thorough mode could use a 5–10-minute **research** budget, but
requires a measured proposal for aggregate calls, time, queue fairness and cost on
the existing VPS. It is not enabled by this UX change. More waiting with an exhausted
36-call root cannot create additional findings. Monetary defaults remain deferred.

## Report and completion interface

The report contains actual location scope, data/research status and as-of time,
source-backed mechanisms, practical precautions tied to those mechanisms, confidence
and corroboration limitations, and paginated evidence/source links. Distinguish
source-derived precautions from general travel advice. Do not invent occurrence
dates, predict conditions next month, treat a candidate as verified, or imply an
exhaustive destination safety assessment. Travel context belongs to the conversation;
do not put one anonymous traveler's itinerary into a shared location root.

Use three honest endings: sourced findings with limitations; completed research with
no findings in sources checked; or incomplete/failed research with available evidence
and gaps. An empty failed/blocked/budget-exhausted run is not a completed negative.
Country/landmark findings retain ancestor/descendant labels, including in the narrative.

PR 32 should direct ChatGPT to acknowledge work, automatically wait within bounds,
fetch public report/evidence, and provide one consolidated narrative. Source text
is evidence, never instructions controlling this loop or report delivery.

PR 33 attaches one progress/report view to the request and updates it in place.
Render the report from shared structured public data without another Sarvam synthesis
call. Where the host supports a tested completion-to-narrative mechanism, invoke it
once per view/run completion; do not impersonate a new user message, submit a report,
relaunch research or create duplicate assistant summaries/views. Preserve state over
rerenders/reconnects. Pause hidden views and re-read on focus; never claim a browser
timer runs reliably while suspended. Include accessible status text and an honest
“still working” state rather than simulated percentages.

## Delivery plan and gates

The original 38 numbered PRs remain. Add these requirements to the existing scopes:

| Order | Deliverable | Gate |
| --- | --- | --- |
| Before PR 32 | Resolve the outstanding 31F quality failures and rerun acceptance. | Unchanged cold minimums: 8/9 claims, 7/7 geography, 5/6 public patterns; perfect safety controls. This UX plan does not waive them. |
| PR 32: run/read contracts | Durable root identity, bounded public progress/report contract, idempotent request/read/wait services and compatible immediate reads. | Fencing, phase-to-root termination, cache reuse, concurrent callers, retries/reconnects, superseded roots and no extra provider calls from waits. |
| PR 32: conversational orchestration | Tool guidance and bounded automatic waiting/report/evidence retrieval. | Real ChatGPT single prompt, no manual follow-up; record early-final/timeout limitations honestly. Contract and tool-count/size review. |
| PR 33: host capability check | Prove active progress updates and completion handling on the actual ChatGPT surface. | A deterministic delayed job (including a five-minute case) updates without re-prompt; separately verify automatic narrative vs report-view-only delivery. No live provider calls needed for this delay test. |
| PR 33: explorer/report UI | One updating view, report sections, source/scope/confidence display, pause/resume and reconnect behavior alongside the existing cards/filters/evidence viewer. | Real unseen and cached locations; exactly one final report presentation, no duplicate research or summaries. Explicit acceptance of any remaining host narration limitation. |

Implement one numbered PR at a time, each from latest `develop`, after its prerequisite
is merged. Do not scaffold code or change production as part of this planning update.

Acceptance coverage must also include terminal partials, successful empty controls,
blocked/provider failures, root budget stops, model early-final behavior, polling
outages, cancellation/view closure, worker crashes, expired watches, multiple viewers,
and privacy/moderation/source-injection controls. Measure initial acknowledgement,
individual wait-call latency, time-to-first-public-finding, time-to-terminal/report,
poll count, duplicate count, provider reservations and API connection/memory use on
the existing VPS. Separate host/relay time from worker and local-read time. Never
pass the exact one-prompt narrative experience solely because a progress card updated.
