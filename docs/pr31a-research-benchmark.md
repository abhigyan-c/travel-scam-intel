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
prompts. Replay validates model, corpus hash and exact prompt/schema fingerprint. Missing
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

Quality targets are intentionally not
CI pass conditions for this baseline PR; existing failures are the subject of the
intermediate work. Tests verify measurement/replay/isolation correctness instead.

## Measured baseline — 2026-11-06

Artifacts: `benchmarks/data/baseline.json`, `sarvam-recording.json`, and
`production-observations.json` in the same directory. The capture used **22 real
Sarvam calls / 7,242 reported tokens**, below the 40-call cap, and no Serper requests.
No production research was triggered. All five historical production jobs were
exported read-only. Identical replay reproduced every original outcome, count,
error and token measurement; additional relevance/empty-extraction counters were
derived by that replay, retaining the original live stage timings.

| Controlled cold-case metric | Baseline | Target met? |
| --- | ---: | --- |
| Grounded positive claim coverage | 5/9 (55.6%) | No |
| Correct geography coverage | 2/7 (28.6%) | No |
| Eligible public pattern coverage | 2/6 (33.3%) | No |
| Negative controls without extracted claims | 6/6 | Yes |
| Withheld cases without public patterns | 9/9 | Yes |

Warm reruns used **zero model calls/tokens** and produced the same outcomes. This
confirms version-valid reuse, including misses, not improvement from continuation.
All acquired claim lineage checks passed (raw hash, source/document link and exact
clean-text quote offsets). Blocked and empty-source controls remained incomplete.
The named-business case produced no claim, so it does not establish live-model
sensitivity handling merely because its public output was empty.

Specific losses:

- All nine positive passages were classified relevant. Four then returned a valid
  empty claims array: Bangkok fare, Chiang Mai fare, Suva fare and named business.
  The pipeline labeled those bounded runs complete/empty. These are benchmark
  false negatives, not provider errors, deadlines or grounding-validation errors.
- The landmark claim was grounded but `Grand Palace in Bangkok` did not resolve.
  It was also marked sensitive by the model, so resolving geography alone would
  not establish that the resulting pattern could be public.
- Missing and conflicting geography remained unresolved as intended. Those cases
  are claim-extraction positives, not geography/publication positives.
- Paris and Rome produced eligible public candidates with source lineage.

The historical production observations remain a separate, harder dataset: Bangkok
initial/enrichment took 25.069/46.080 seconds, retained one claim each but no
hypotheses, and recorded unresolved-location/grounding/fetch failures. Chiang Mai's
measured refresh spent 10.331 seconds discovering, 16.073 ingesting and 9.671
extracting, with three grounding rejections and no claims. Older jobs lack detailed
telemetry; unknown counts/usage are null, not zeros. These observations were not
rerun under controlled search results and cannot be compared as cold/warm trials.

The short synthetic corpus does not reproduce production article length, source
ranking, multi-passage starvation or every moderation issue. It exposes repeatable
failures and a measurement protocol; 31C should add a separately versioned long-
article/provider diagnostic set. Preserve this baseline rather than tuning its
labels to fit the model. PR 31B should address the landmark hierarchy; PR 31C must
investigate valid-empty false negatives as well as grounding and sensitivity.

Validation: **854 passed, 10 skipped** with infrastructure enabled; Ruff lint/format
passed. The committed provider recordings also replay through the actual pipeline
in integration tests. Isolation tests check that public claim/location counts and
the set/count of generated schemas remain unchanged after cold/warm runs.
No application code, migration, public contract or deployment behavior changed.
