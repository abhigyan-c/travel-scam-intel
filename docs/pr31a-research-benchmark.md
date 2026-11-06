# PR 31A — Research benchmark

Branch `codex/pr-31a-research-benchmark`, base/target `develop`. The user authorized
intermediate PRs after PR 31, before PR 32. This PR measures existing behavior;
location, extraction, acquisition and continuation changes belong to 31B–31E.

## Protocol fixed before baseline capture

Fifteen original synthetic cases cover five cities (Bangkok, Chiang Mai, Paris,
Rome, Suva): explicit city claims, a landmark in a city, missing/conflicting
geography, a named business, ordinary tourism/prices/closure, malicious source
instructions, blocked acquisition and an empty page. These are evaluation material,
not real allegations. Development/holdout labels reserve cases for later changes;
the small visible corpus is not an independent statistical validation set.

Each case runs the actual research, processing, grounding, geographic resolution,
claim-to-hypothesis/scoring and public-read services. Search/fetch/storage use local
fixtures. Every cold case gets its own generated PostgreSQL schema; warm reruns
reuse exactly those acquired bytes/derived records. Schemas are dropped afterward.
Production schemas, R2 objects and traveler reports are never modified. Only
loopback database hosts are accepted. No embedding model is downloaded; empty-scope
and exact matching are exercised, not semantic retrieval quality.

Targets for 31F, fixed before measuring this baseline:

| Metric | Target |
| --- | ---: |
| Positive cases with a grounded quote containing labeled mechanism | ≥80% |
| Correct full geography among cases with labeled resolvable scope | ≥90% |
| Eligible cases yielding public patterns | ≥80% |
| Negative cases without extracted claims | 100% |
| Cases requiring withholding without public patterns | 100% |

Report numerator and denominator separately. A missing denominator is not a pass.
Safety includes blocked/empty controls, but acquisition failures must remain
incomplete, not successful negatives. Evidence-term checks measure source support,
not the correctness of every generated summary; human inspection remains necessary.
Small counts and a single stochastic provider run cannot establish global reliability.

## Commands

Start local infrastructure with `docker compose up --wait`. From the repository:

```powershell
# Default: recorded responses, no external search/fetch/model calls.
.venv/Scripts/python.exe -m benchmarks.research --output .tools/research-replay.json

# Explicit capture: existing ignored .env SARVAM_API_KEY, at most 40 model calls.
.venv/Scripts/python.exe -m benchmarks.research --mode live --max-live-calls 40 `
  --recording .tools/new-sarvam-recording.json --output .tools/new-research-baseline.json
```

Live capture records only completions to synthetic passages, with input/schema
fingerprints, token counts and duration; it omits request IDs, credentials and full
prompts. Replay validates corpus hash and exact prompt/schema fingerprint. Missing
records stop execution rather than becoming a quality failure or calling a provider.
Prompt/schema changes require a separately named new recording; preserve the baseline.

Replay execution timings exclude recorded provider latency. `provider_seconds`
reports historical call time and must never be presented as a new latency benchmark.
Warm is a reprocessing/cache experiment, not an MCP fresh-location response timing.
Token usage is measured; currency estimates are omitted because no verified billing
rates are supplied. Search fixture credits are zero, not an estimate of real Serper cost.

`benchmarks/snapshot_research.py` optionally exports recent Bangkok/Chiang Mai
production jobs through an explicit read-only transaction. It exports stage/error
counts without claim text, source URLs, quotes, credentials or provider responses.
Saved production observations provide real acquisition/deadline evidence; they are
observational and separate from the controlled synthetic benchmark.

## Review artifacts

Baseline results, provider recording, production observations and measured findings
will be recorded after the first bounded run. Quality targets are intentionally not
CI pass conditions for this baseline PR; existing failures are the subject of the
intermediate work. Tests verify measurement/replay/isolation correctness instead.
