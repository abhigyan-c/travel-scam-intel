# PR 33 — ChatGPT scam explorer

Branch: `codex/pr-33-scam-explorer`, based on merged PR 32 acceptance in `develop`
(`eccac86`). The production release remains PR 32 until this branch is reviewed,
merged and deployed. Actual ChatGPT UI acceptance remains open.

Update — 2026-11-22: PR 33 and PR 34 are deployed together at `65a828b`. Real ChatGPT
automatic narration over a controlled five-minute run passed, but the active
explorer's terminal view was not independently identified/verified. Further manual
testing was deferred by the user; do not infer a confirmed UI defect or a passed
explorer gate from static conversation cards. See [acceptance](pr34-deployment-acceptance.md).

## Interface

`request_scam_report` attaches `ui://travel-scam/explorer-v1.html` using standard
MCP Apps metadata. All ten existing tools retain their response shapes and remain
usable by clients without UI. Get/wait/evidence responses have no attached resource,
so polling updates one existing view. The resource is self-contained HTML/CSS/JS;
Docker already copies these files with `app/`. There are no external assets, direct
browser API requests, new hosting requirements or frontend runtime dependencies.

The view shows progress, retained coverage and source freshness separately; stop
reasons, unknown dates and candidate disclaimers remain visible. It renders public
mechanisms and source excerpts, not another LLM-generated report. Filters cover
confidence label, scope, actual area, category and source freshness; sorting covers
confidence, last source check and observed report count. These apply to the displayed
page of up to 20 findings, explicitly not the entire destination dataset. Evidence
is separately paginated in pages of five. Parent/child findings retain actual scope.
Source checks do not become incident dates; counts are not prevalence or independent
corroboration. No synthetic percentage or completion ETA is displayed.

One new tool, `watch_scam_report(run_id)`, is app-only: eleven server tools, ten
model-visible tools in a conforming host. Its non-read-only annotation accurately
describes creation of operational watch metadata; it is not idempotent. It can only
follow an existing run and cannot queue research, submit an incident or increase
provider allowances. The view receives a separate 600-second/60-wait watch so its
reads do not consume PR 32's model watch. Waits are at most five seconds, sequential,
with at least the returned next-check delay (one second today). The view additionally
persists its wall-clock deadline and call count. A caller with rapid revisions may
reach the call bound before ten minutes; the UI stops honestly and offers Resume.

Pause, view closure and hidden-tab suspension stop the watcher, not shared research.
An admitted request can finish while hidden, but no further poll is scheduled.
Reconnection re-reads the same root and its current publication eligibility; it never
requests new research. Session storage contains only run/watch addresses, deadlines,
counters, filters and paging, not source text or traveler itineraries. Expiry,
capacity and outages stop automatic calls and retain available data with a retry
notice. Explicit Resume can create another bounded watch of that same run. Superseded
roots stay on their original membership. Terminal rendering does not create a new view.

Storage is namespaced by the host's tool-invocation ID when supplied, keeping two
views' watches separate. A host that omits that identity falls back to a shared
same-run session entry; viewers can then share/exhaust the same bounded watch.
This conservative fallback never silently extends an expired watch. Verify actual
ChatGPT invocation identity and reconnect behavior at the host gate.

## Host protocol and narrative limitation

The bridge implements `ui/initialize`, result/context notifications, `tools/call`,
size notifications and graceful teardown. It accepts messages only from its parent,
uses request IDs/timeouts, checks tool-proxy capability and releases pending requests
and observers on closure. Host theme variables and safe-area insets are supported.
Where advertised, compact structured view context is updated for future model turns.

The current documented follow-up mechanism sends a **user-role** message. The view
does not synthesize such a message or claim it can wake a finished assistant turn.
PR 32's model request/wait/report narration stays intact during the active turn;
the UI can independently show final structured results after a model early-final.
That distinction requires real ChatGPT testing and explicit acceptance of any remaining
narration limitation. A local updated card alone does not pass that gate.

Protocol references checked 2026-11-21:
[OpenAI UI guidance](https://developers.openai.com/plugins/build/chatgpt-ui),
[MCP Apps specification](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/draft/apps.mdx),
[OpenAI message extension](https://github.com/openai/mcp-extensions/blob/main/docs/spec.md#uimessage-extensions).

## Publication and safety

The resource requests no external connection, resource or frame domains. All reads
go through existing public services and reapply their moderation/eligibility rules.
Source strings render with `textContent`, never HTML/Markdown execution. Links accept
only absolute HTTP(S) URLs without embedded credentials and use `noopener noreferrer`.
No attachments, private reports, admin content or provider credentials enter the UI.
The UI has no submission or research-start control. Evidence is cleared on report
revision, hidden/focus changes and teardown; opening it always reads current eligibility.

## Local verification and remaining release gate

Run Python checks normally and `node --test tests/ui/*.test.mjs` (Node 22, no npm
install). CI runs both. Controller cases include a simulated five-minute completion,
exactly one completion transition, cached/terminal reads, partial/failure/negative
endings, pagination, watch exhaustion, wall-clock suspension, pause/resume/reconnect,
poll failures, teardown races, scope filters, safe links and parent-only bridge calls.
The real database test verifies the app watch creates no root/job/provider work and
does not consume the original model watch.

Local browser verification on 2026-11-21 exercised the actual HTML resource in the
mock host: a real-time 300-second run reached terminal partial results automatically
after 52 tool calls (one read, one watch creation, 50 waits), with zero research
requests. The old preview wait display lagged its current call by one; that display
bug was fixed. Narrow/tablet/desktop layouts, filtering, evidence, reconnect outage,
fresh eligibility reads after withdrawal and literal injection strings were inspected.
The browser check also caught and repaired a native timer-binding error not caught
by the injected-clock tests. Unchanged polls now preserve selector/button focus.
Unverified cached initial cards remain hidden when the reconnect read fails.
Sanitized observations and the screenshot are in `benchmarks/data/33/`.

Final validation: **985 Python tests passed, 10 skipped; 11 Node tests passed**.
Ruff lint/format and whitespace checks passed. The production Docker image built,
and its packaged HTML/resource read succeeded (approximately 28 KB, no missing
bundle placeholders). No migration or research-policy change is required.

For a loopback browser preview, run:

```powershell
.venv/Scripts/python.exe -m benchmarks.explorer_preview
```

Open `http://127.0.0.1:8033/`. Its banner clearly identifies synthetic data and a
mock host; it has no DB, real allegations, credentials or provider access. The same
production resource can be inspected at 375/768/960 pixels, with a real-time five-minute
delay, outages, source withdrawal and reconnect controls. This is not an actual
ChatGPT capability result. Do not expose this development server as production.

After merge/deploy, refresh the ChatGPT connection, verify UI resource discovery,
the eleven-tool server/ten-tool model distinction, and one natural request for an
unseen destination followed by a cached read. Inspect actual run/watch/job membership,
no duplicate roots, evidence scope and publication restrictions. Separately run a
deterministic delayed fixture, including five minutes, on the actual ChatGPT surface
in an isolated test environment with no provider calls or production data. Record
initial acknowledgement, individual reads/waits, first finding and terminal latency,
poll/duplicate counts, provider allowances and VPS connection/memory samples. Do not
pass live acceptance on the local simulator or PR 32's narrative result alone.

Also verify hidden/focus handling, two viewers, disconnect/reconnect, user cancellation,
worker failure, expired watch, capacity and source-injection controls. Record whether
the assistant narrated automatically or only the view finished; explicit acceptance
is required if the latter remains. No new public test tunnel is enabled by this PR.
