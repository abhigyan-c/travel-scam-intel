# PR 31D — Source acquisition quality

Branch `codex/pr-31d-source-acquisition-quality`, base/target `develop` after
PR 31C's squash merge `e0d1022`. This PR changes discovery/acquisition, not extraction,
moderation, scoring, persistence/continuation or the public MCP contract.

## Policy

- `discovery-v2` runs at most two searches concurrently under the existing shared
  discovery deadline and query count. Results/usage are assembled in query order,
  not completion order. Finished results survive slow queries; unstarted timed-out
  queries report zero requests. Lease loss/cancellation cancels and awaits siblings.
- Keep the broad first query; queries two/three add `travel blog` intent. Later
  enrichment and hypothesis-topic queries remain available. This is source selection,
  not a claim that blogs are authoritative or independent evidence.
- Deduplicate known advertising/tracking parameters within discovery while retaining
  the best original URL for acquisition/provenance. Resource parameters such as `id`
  remain distinct. No existing source URLs or database identities are rewritten.
- Try one candidate per host before additional pages from that host, preserving its
  internal provider rank. Diversity applies before candidate truncation. Host grouping
  is exact hostname with `www.` normalized, not a registrable-domain classifier.
- Failure feedback lasts only for one research run. Two consecutive 401/403 denials,
  two transport/acquisition timeouts, or two processed-empty pages defer later pages
  from that host. One private page/404 does not block the host; successful acquisition
  resets access/transport failures. Successful processing resets empty-page failures.
- A 429 stops immediate native retry and defers that host for this run. Redirects
  cannot reach a run-deferred host: the guard runs before its DNS/HTTP request.
  A denied redirected host does not condemn the origin's other pages. No source is
  permanently blacklisted; cross-job backoff/progress belongs to later work.
- Fetch-only work has a configurable six-second ceiling, bounded to ≤10 seconds.
  Object storage/DB/processing do not consume that origin cap. The overall 25/45
  second job limits and logical fetch/query/model-call limits are unchanged.

`APP_RESEARCH_MAX_FETCH_SECONDS` configures local application settings;
`RESEARCH_MAX_FETCH_SECONDS` in the production host configuration is passed through
Compose, defaulting to 6 without new provisioning. Old job payloads use this default.
Existing URL/DNS/public-IP/redirect/TLS/type/size protections remain in place. There
is no browser scraping, proxy rotation, access-block bypass or new paid service.

Returned-but-unusable search results now produce incomplete research, not a true
empty search. Deferrals are explicit `host_deferred` diagnostics; their count is
not labeled as a deadline. Failed/empty acquisition cannot produce invented claims.
HTTP status and safe fetch error codes are retained alongside existing stage timings.
No raw HTTP error body or credential is included in diagnostics.

## Controlled comparison

`benchmarks/data/31d/acquisition-cases.json` defines synthetic source outcomes and
**virtual** timing assumptions. These are not real website latency measurements.
The comparison uses the same logical fetch/time bounds for both policies.

| Case | Baseline → revised distinct readable documents | Other observation |
| --- | ---: | --- |
| Repeated denials dominate rank | 3 → 5 | Eight → seven logical attempts |
| One valid host | 4 → 4 | No source loss |
| Individual missing/private pages | 2 → 2 | Later healthy page still tried |
| Repeated empty pages | 0 → 2 | Same four-attempt limit |
| Tracking duplicates | 2 → 2 | Three → two attempts |
| Rate-limited host | 2 → 2 | Five → three attempts |
| Slow origin | 0 → 2 | Fetch cap leaves budget for other hosts |
| Late recovery after two failures | 1 → 0 | Deliberate conservative loss; remains incomplete |

The last case is a real policy tradeoff: this heuristic does not dominate every
possible source sequence. Deferred work can be reconsidered on a subsequent run;
this PR does not add an unbounded retry loop or pretend deferred pages are empty.

## Live observations — 2026-11-07

Three bounded development probes used existing Serper credentials: three searches
and at most eight fetches each, no LLM calls, DB writes or R2 writes. Bodies/cleaned
text were discarded; URLs, hashes, outcomes and elapsed times are retained. The
Droplet probes loaded candidate modules in a separate API-container Python process;
the running application and filesystem were unchanged. They are developmental
observations with source hashes, not measurements of a deployed release.

| Probe | Discovery | Processable English / eight attempts | Empty | HTTP/fetch errors |
| --- | ---: | ---: | ---: | ---: |
| Workstation, broad queries | 2.954 s | 2 | 3 | 3 |
| Droplet, broad queries | 3.411 s | 1 | 1 | 6 |
| Droplet, broad + article-intent queries | 3.253 s | 3 | 1 | 4 |

The selected mix retains the first broad query and appends `travel blog` to the
second/third, exactly as tested. Some returned processable text came from YouTube;
`ready` means parsable English, not a useful article, grounded claim or verified scam.
Source accessibility remains a material limitation. Live ordering/content/egress
varied, and these are single samples, not a controlled causal latency comparison.
The sum of individual search durations is only an arithmetic serial reference,
not an actual measured serial run. No production speed SLA or claim-quality gain
is inferred from this acquisition-only probe.

## Reproduction and review

```powershell
# Offline controlled comparison; does not contact any service.
.venv/Scripts/python.exe -m benchmarks.acquisition --output .tools/acquisition.json

# Explicit bounded paid-search probe, no model calls or data writes.
.venv/Scripts/python.exe -m benchmarks.acquisition --live --output .tools/live-acquisition.json

# Same probe from the existing Droplet, isolated process rather than deployment.
.venv/Scripts/python.exe -m benchmarks.acquisition_remote --live --output .tools/vps-acquisition.json

# Frozen v3 extraction corpus: local DB and recorded responses only.
.venv/Scripts/python.exe -m benchmarks.research --output .tools/31d-pipeline-replay.json
```

Live probes require new output paths and use the existing SSH alias/container or
local environment. Remote helpers never export server credentials. The default
offline corpus and frozen extraction replay need no provider key. No further live
calls are needed to inspect the committed measurements.

Validation: **912 passed, 10 skipped** with infrastructure enabled; Ruff lint/format
passed. Tests cover native fetch safety, parallel bounds/cancellation, 429 retry
behavior, redirect deferral, IPv6 host identity, tracking/resource parameters,
run-local resets, fetch-only caps and retained partial-result/source lineage.
One existing true-empty fixture now correctly reports zero raw returned results;
an unparseable-returned-results regression verifies the separate incomplete case.

No production deployment or bulk reprocessing is performed. PR 31E remains separate.
Acquisition improvements do not resolve v3 extraction misses or pass the 31F gate.
