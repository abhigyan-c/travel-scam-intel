# PR 31F: research acceptance — measured, not passed

Branch `codex/pr-31f-research-acceptance`, base/target `develop` at
`fc936c436d3a49804f042f3e38cdce3ad4390e14` (merged PR 31E).
Measurements and the real ChatGPT review were performed on 2026-11-08.

**Overall gate: FAIL.** Deployed traceability, safety controls, recording reproduction
and the real ChatGPT flow pass. The frozen positive quality targets still fail.
Do not treat this PR as permission to start PR 32 or lower the existing thresholds.

## Deployment and isolation

Production previously ran `13cdcb20fd34e7987ad0a9241a450ef9e6113ad9`.
Built the merged develop application, pushed the immutable image/archive over the
private Tailscale SSH connection and activated it with the existing root-owned
deployment entry point. The API and worker now both run `fc936c4`; migrations and
seeding completed, all four containers became healthy, and readiness checked
PostgreSQL, Redis and private object storage successfully.

The existing filtered public endpoint remains
`https://travel-scam-prod.tailbbf557.ts.net/mcp`. No new public listener, secret,
provider, proxy or browser acquisition service was added. Old API/worker processes
were replaced before exercising the new cursor implementation.

This PR adds acceptance tooling/artifacts/docs, not application behavior changes.
The deployed image is the already-merged prerequisite, not unreviewed benchmark code.
Controlled synthetic data ran only in generated local PostgreSQL schemas and
in-memory source/storage fixtures; those schemas were removed. No synthetic scam
reports or test allegations were inserted into production.

## Frozen controlled quality

The original 15-case corpus, labels, development/holdout split and denominators from
31A are unchanged. A new separately named Sarvam recording exercises the current
pipeline. Its capture used **22 real synthetic-passage completion attempts** (20
cold, 2 warm), below the 40-call ceiling, with **8,448 reported tokens**. At least
one interrupted response has unknown usage; that token total is not complete billing.
No Serper requests were used for this controlled capture. Replay made no provider
requests and reproduced outcomes/counts/errors/usage exactly.

| Metric | Required | New cold | New warm | Result |
| --- | --- | --- | --- | --- |
| Grounded positive claims | ≥80%, hence ≥8/9 | 7/9 | 8/9 | Cold fails |
| Correct resolvable geography | ≥90%, hence 7/7 | 6/7 | 6/7 | Fails |
| Eligible public patterns | ≥80%, hence ≥5/6 | 4/6 | 4/6 | Fails |
| Negative claim safety | 6/6 | 6/6 | 6/6 | Pass |
| Withholding safety | 9/9 | 9/9 | 9/9 | Pass |

All retained claim lineage checks pass. Blocked and empty-source cases remain
incomplete, rather than becoming successful negative research. The Bangkok city
fare passage still yields a valid-empty false negative. The missing-geography cold
attempt is incomplete and succeeds on a warm retry; that does not repair a cold
failure. Rome remains held by the model and has no public pattern. Named-business
claims remain held. No allegation was approved to improve a score.

Relative to 31A's 5/9 claims, 2/7 geography and 2/6 eligible patterns, useful coverage
has improved. Relative to 31C/31E's 7/9, 5/7 and 4/6, this capture varies in geography
but still fails the same overall gate. One stochastic capture on a small visible
corpus is not evidence of statistical/global reliability. No repeated sampling or
label/prompt tuning was performed to obtain a passing result.

## Live production traces

Bangkok was a previously researched, stale popular destination with no public
findings before this check. Its MCP request started one refresh root. Suva had
never been researched, confirmed by a read with research disabled; the user's
ChatGPT request started its first initial root. Polling used research disabled.
The snapshot exporter reads production DB transactions read-only and reads existing
raw objects only for public evidence verification; it does not queue jobs or write
objects. Current-root association uses the cursor's newest-N-job count and saved
document IDs, not a reconstructed historical root join.

| Measurement | Bangkok | Suva |
| --- | ---: | ---: |
| Root span, including queue/finish overhead | 82.232 s | 90.233 s |
| Phases | refresh, continuation, continuation | initial, enrichment, continuation |
| Attempts per phase job | 1 | 1 |
| Search reservations / credits reported | 6 / 6 | 3 / 3 |
| Logical source fetch reservations | 20 | 28 |
| Eligible-document reservations | 7 | 11 |
| Completion reservations | 24 | 36 |
| Reported completion tokens, incomplete usage | 31,114 | 39,705 |
| Ingestion stage time | 27.756 s | 34.808 s |
| Classification stage time | 9.570 s | 21.277 s |
| Extraction stage time | 22.749 s | 17.130 s |
| Median local REST read, 3 requests, research disabled | 0.0993 s | 0.1013 s |
| Public patterns retained | 1 | 1 |
| Final overall research state | stale / incomplete | stale / incomplete |

All cumulative reservations stay within the root caps; each root stops at three
phase jobs. Logical fetch counts are not a count of redirect/transport HTTP attempts.
Neither root is represented as completed research or proof that other scams do not
exist. The measured spans are **not** time-to-first-finding; intermediate polls were
not frequent enough to establish that metric.

The observed public connector calls took roughly 10–37 seconds in this client's
wall clock, including connector/relay overhead. The local REST reads exclude those
layers. Do not subtract these numbers to infer Funnel-only latency or call this an
end-to-end speed improvement. Historical PR 31A production jobs used different
inputs/phases/search rankings and are not a matched timing control. Source access
restrictions, empty pages, unresolved places, held claims and passage/resource caps
still prevent complete research. Background acquisition and LLM work remain material.

Two genuinely sourced findings survived. Both have verified raw-object hashes,
clean-text hashes, exact quote offsets, extraction/source/document lineage and
publication eligibility, with documents acquired by the current root:

- Bangkok search: Grand Palace bird-feeding overcharging, actual scope **Grand Palace
  POI**, `descendant`; source [Your Thai Guide](https://yourthaiguide.com/top-10-bangkok-scams-you-should-avoid/),
  `SECOND_HAND`. It is not a citywide Bangkok observation.
- Suva search: taxi overcharging, actual scope **Fiji country**, `ancestor`; source
  [Travel With Hello](https://www.travelwithhello.com/blog/fiji-safety-travel-guide),
  `UNKNOWN`. It is not Suva-specific evidence.

These are single-source candidate patterns, not verified incidents or prevalence
estimates. Confidence remains limited; no occurrence/publication date was invented.
Private/held claim text is not exported. Public snapshots include only already-public
cards and evidence identifiers/checks; provider request identifiers are omitted.

## Real ChatGPT review

The user tested the existing custom MCP server in a new ChatGPT conversation and
pasted its rendered responses. First, Suva resolved exactly to its Fiji city record,
queued research, then returned `research_incomplete` on a non-requesting follow-up.
It did not invent claims, claim a successful negative, or submit a report.

After the worker finished, a second prompt read Bangkok and Suva with research
disabled. ChatGPT returned one candidate each, fetched/cited their source evidence,
preserved the descendant/ancestor distinction above, and explained stale/incomplete
research, limited confidence, undated single-source evidence and candidate status.
This passes the host read/follow-up/evidence/scope gate. It is user-reported rendered
evidence, not an independently captured host tool-event log or timing benchmark.
The connected Codex MCP tool calls were recorded separately and are not substituted
for this user ChatGPT test.

## Reproduction and artifacts

Artifacts are in `benchmarks/data/31f/`:

- `sarvam-recording.json`, `controlled-live.json`, `controlled-replay.json`:
  fresh synthetic capture and exact reproduction.
- `live-snapshot.json`: bounded read-only production root/usage/provenance trace.
- `mcp-connector-trace.json`: public connector requests/results, including before
  research, queueing and later evidence reads; client wall time labeled separately.
- `chatgpt-review.json`: structured notes from the user's two rendered responses.
- `acceptance.json`: all checks pass except **quality**; `accepted=false`.

```powershell
# Replay the captured responses; no new provider calls.
.venv/Scripts/python.exe -m benchmarks.research --mode replay `
  --recording benchmarks/data/31f/sarvam-recording.json --output .tools/31f-replay.json

# Read existing production state; never starts research.
.venv/Scripts/python.exe -m benchmarks.live_snapshot `
  991d6b7e-f69b-5158-bf6d-e8c9f3ec871a 7eaf771e-8bf9-5d7a-b778-c00aa3ccecd0 `
  --remote --output .tools/31f-live.json

.venv/Scripts/python.exe -m benchmarks.acceptance `
  --controlled benchmarks/data/31f/controlled-live.json `
  --replay benchmarks/data/31f/controlled-replay.json `
  --live benchmarks/data/31f/live-snapshot.json `
  --chatgpt benchmarks/data/31f/chatgpt-review.json --output .tools/31f-acceptance.json `
  --require-pass
```

The last command intentionally exits nonzero while quality fails. The report builder
recomputes the scorecard, rejects changed labels/corpus, missing/duplicate cases,
changed replay outcomes, over-budget traces, old-root/held/unverified evidence and
false `nothing_found` labels. It cannot promote a protocol-only success to a host
test or count an empty evidence set as provenance success. Tests check these failure
modes rather than asserting that the current quality measurements pass.

Validation: **934 tests passed, 10 skipped** with local infrastructure enabled;
Ruff passed. The merged release image built and deployed successfully. Final
production checks still showed all containers healthy, all readiness dependencies
OK and about 2,682 MiB memory available. `--require-pass` was exercised and returned
exit status 1 with the sole blocker `quality`, as intended.

## Required before PR 32

Repair the remaining source-grounded extraction false negatives and unjustified
sensitivity holds in separately scoped work without weakening quote grounding or
moderation. Cold coverage must reach at least 8/9 claims, 7/7 geography and 5/6 public
patterns on the unchanged corpus while keeping both safety controls perfect. Then
repeat recording reproduction and the deployed trace/host gates for that release.
Do not manufacture a Suva-specific result from country-wide evidence. Broader
evaluation, acquisition reliability and end-to-end transport performance still need
the later evaluation/hardening work; this small acceptance check does not replace it.
