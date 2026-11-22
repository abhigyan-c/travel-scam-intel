# PR 33/34 deployment and ChatGPT acceptance — 2026-11-22

Merged PR 34 (`65a828b026127497479990a3a3dc85cd32b26059`, including PR 33) replaced
production PR 32 (`f7e5692`) through the existing private Tailscale push deployment.
An encrypted offsite backup completed successfully before activation. Matching API
and worker images are healthy; PostgreSQL, Redis and storage readiness passed and
`alembic check` found no pending model changes. The public hostname discovers twelve
tools and two UI resources. No research/provider allowances were increased.

## What passed

- The public MCP smoke verified discovery, both resources, immediate reads, missing
  resource errors and rejection of unconfirmed reports without starting research.
- The user refreshed ChatGPT and confirmed actual interactive production report-form
  controls appeared, with Bangkok preselected. No incident was submitted to production.
- On an isolated acceptance connection, one synthetic five-minute root transitioned
  from queued/researching to completed/nothing-found. ChatGPT automatically produced
  the final written result without another user prompt. The model watch admitted
  45 waits and the separate app watch admitted one, all below the 60-wait bound.
  The root ran from 04:35:31.844658 to 04:40:31.844658 UTC, exactly 300 seconds.
- In a separate isolated reporting phase, the user operated the actual report form,
  confirmed its synthetic contents, encountered a deliberately lost receipt after
  commit, and retried the same reviewed submission. Two attempts produced one report,
  one normalized document and one durable processing job. The user supplied the
  actual replayed receipt from the app. No hypotheses or evidence were published.
- Subsequent production counts showed zero reports and zero new research roots on
  the test date. The synthetic cases never entered production data or R2.

## What remains unverified

The actual explorer's terminal DOM/update behavior is **not accepted**. The user
initially said a widget stayed queued, but the text supplied was ChatGPT's static
“Research watch / Initial findings” conversation card, not the explorer template
(which has “Findings and precautions,” filters and Pause updates). The later pasted
“Final result” was also written conversation content. Backend records establish
automatic narration and some app read/watch activity, not what the visible explorer
rendered. There is insufficient evidence to declare either an explorer defect or
successful active-view completion. No speculative production UI repair was deployed.

The user deferred the final active-explorer retest. Real unseen/cached production
explorer/evidence interaction and the complete customer-facing review gate remain
open. A written final report or local preview is not a substitute for that gate.
Do not mark all of PR 33's actual-host requirements passed from this evidence.

The agent's Google Chrome selection was rejected by automatic approval review:
“Computer Use was not approved to use Google Chrome.” The user operated ChatGPT;
the agent verified protocol responses, counters and database isolation. No workaround
was used to bypass browser access restrictions. No host screenshot/console trace or
initial acknowledgement timing was captured.

## Isolation and measurement limits

`benchmarks/chatgpt_acceptance.py` served the same production UI assets and MCP
tool functions. A bounded report adapter supplied the deterministic empty five-minute
fixture; it was not real scam research and did not measure production worker yield.
Reporting used the actual intake with a disposable **localhost** PostgreSQL schema,
private in-memory raw-object storage and a non-executing queue adapter. Serper/Sarvam
credentials were empty. The `--receipt-loss` phase altered only the first successful
test receipt to simulate uncertainty after the real isolated commit.

A private Tailscale SSH reverse forward joined the local test server to a temporary
exact `/mcp/acceptance` route under the already authorized filtered hostname. It
exposed only MCP, not the private `/stats` endpoint, REST, admin or storage. There
was no new Funnel port, production image modification or production test record.
The first two calls in the progress trace were operator discovery/preflight calls;
they should not be counted as ChatGPT actions. Call durations are server-side tool
times, not full host/relay or acknowledgement latency.
The reporting trace also contains a missing-run read after the deliberate fixture
reset, which invalidates its in-memory test run IDs. Production roots were not reset.

Production resource figures are point samples, not peak/load-test results. A sampled
worker startup spike settled to 0.21% CPU / 97.62 MiB; API memory was approximately
337–350 MiB. The synthetic waits ran locally through the relay and cannot establish
VPS database load under a real multi-viewer research workload.

## Cleanup and resumption

After the user stopped further testing, the temporary route was removed and Caddy
validated/reloaded. `/mcp/acceptance`, public REST/admin/docs returned 404; `/health`
returned 200. The SSH forward and local server were stopped. Windows interruption
left disposable schemas behind, so all three explicitly identified localhost
acceptance schemas were removed and a namespace audit confirmed none remained.
The synthetic report/document/job and memory-only raw objects are gone. The local
database service was stopped. Production remains on `65a828b`.

The temporary “TSI Acceptance — test only” ChatGPT connection is no longer usable;
it may be removed by the user. Retain the normal production connection.

When testing resumes, recreate a fresh isolated fixture and verify the actual
explorer's own status text with its filters visible throughout the delay; capture
the component rather than conversation cards. Then check actual production cached
evidence/scope and an unseen-location view within existing research bounds. Never
reuse synthetic cases as travel intelligence or submit fake incidents to production.
The harness binds loopback and uses a fixed local test database; it is not deployed
by the production Dockerfile. Use an explicit Selector event loop on Windows. If a
forced shutdown leaves schemas, audit and remove only the exact generated local
`chatgpt_acceptance_<32 hex>` schemas from that test after its server is stopped.

Sanitized traces and outcomes are in `benchmarks/data/34/`:
`chatgpt-progress.json`, `chatgpt-reporting.json`, `production-sample.json`, and
`deployment-acceptance.json`. Draft text, idempotency keys, raw objects and credentials
are not included.
