# PR 36 — Intelligence evaluation harness

Branch `codex/pr-36-evaluation-harness`, based on merged `develop` at `33c430d`.
This adds development/CI tooling, not a deployed service, migration or product endpoint.
It does not start PR 37 or reopen the deferred ChatGPT explorer test.

## What is evaluated

`benchmarks/data/36/corpus.json` contains 49 original fictional cases. Labels were
authored independently before the provider capture. Real place names provide geography
context; the described incidents and named business are synthetic, not travel advice.
Both development and holdout partitions cover every task. These are small regression
partitions, not an independent population sample or a claim of statistical accuracy.

| Task | Cases | Adapter / labels |
| --- | ---: | --- |
| Claim extraction | 8 | Actual source-bound `extract_passage` and grounding validation; labeled mechanism term groups, evidence fragments, absent dates and entity/sensitivity policy. Four positive and four negative documents. |
| Location resolution | 8 | Actual DB resolver: exact, qualified, ambiguous, provisional, missing and conflicting-parent inputs; expected status and full hierarchy. |
| Hypothesis matching | 9 | Actual `resolve_hypothesis`, candidate scope filtering and reviewed lifecycle application; expected decision, target and stored evidence stance. |
| Duplicate detection | 6 | Production pair comparison, with copy, independent, short-quote, context-conflict, cross-post and anonymous examples. |
| Duplicate groups | 2 | Production complete-link grouping with explicit expected claim memberships; incompatible context must not bridge groups. |
| Independence | 4 | Production contribution scores against independently specified bounds for documented, shared-author, unknown-origin and confirmed-group cohorts. |
| Confidence | 5 | Production confidence/eligibility rules: independent support increases credit; copies cannot inflate it; contradiction, held evidence and geography remain distinct. |
| Scheduler | 7 | Production priority, tier, reservation and budget-fit helpers: hot/cold, uncertainty, first deep enrichment, all budget dimensions, demand decay, cooldown and dormancy. |

Matching uses a **controlled vector provider**, not the trained embedding model. All
in-scope candidates get current unit vectors so the test isolates semantic comparison,
geographic exclusion and lifecycle/stance behavior. Each matching case currently has
one supplied candidate. This is not a benchmark of retrieval ranking, competing-target
selection, the pinned embedding model's quality, vector latency or model cold starts.

Stance is the actual attached evidence stance after a reviewed resolution. In this
application, RELATED/SUBTYPE create their own child hypothesis with supporting evidence;
they do not attach a `related` vote to the candidate. CONTRADICTS attaches contradicting
evidence to the existing pattern. These distinctions are explicit in the labels/report.
The suite does not introduce a separate invented stance classifier or auto-approve
semantic decisions in production.

The scheduler fixtures evaluate deterministic policy/admission calculations, not live
worker cycles. Existing scheduler integration tests continue to cover leases, concurrent
cycles, daily reservations and durable enqueueing. Existing pipeline, public-eligibility
and embedding tests also remain necessary; this harness does not replace them.

## Reproduce the baseline without provider calls

Start the ordinary local PostgreSQL service, then run from the repository root:

```sh
docker compose up -d --wait postgres
uv run --locked python -m benchmarks.evaluate --mode replay \
  --baseline benchmarks/data/36/baseline.json \
  --output .tools/evaluation/report.json
```

The same command works on PowerShell as one line. The runner uses a Windows-compatible
selector event loop. It creates a random disposable schema, explicitly creates every
table in it, and drops that schema in `finally`, including on recording misses.
Fixture transactions roll back between cases; provisional locations and lifecycle
records cannot contaminate later cases. Fixed fixture timestamps and UUIDs make the
provider request fingerprints reproducible across schemas and hosts.

The default DB configuration disables `.env` loading; explicit environment values,
the normal configured secret directory or `--database-url` may override it. The schema helper rejects non-loopback DB hosts
before connecting. Use a genuinely local development database, never a forwarded
production connection. No production API, Serper, R2, Redis worker, embedding download
or LLM endpoint is used in replay.

The infrastructure CI job runs replay after integration tests and uploads only the
synthetic JSON report. No provider credentials are supplied. The normal test suite
also checks metric integrity, policy regressions and exact replay of the real adapters.

## Scoring and failure rules

Reports include per-case observations and checks, denominators, per-task and per-split
scores, extraction precision/recall, matching confusion matrices, explicit false-merge
and false-split counts, stored stance accuracy, source/harness hashes and model/policy
versions. Model-call/token counts are identified as recorded consumption; replay
explicitly reports zero live calls. Replay timing is not presented as model latency.

Extraction pairs predictions to labeled mechanisms one-to-one, requiring the expected
quote fragment and term-group coverage as well as production grounding checks. An
extra claim is a false positive; a missed mechanism is a false negative. This lexical
proxy does not establish semantic equivalence or allegation truth. The v1 date cases
are all undated: they test unknown-date preservation, not date parsing accuracy.

A false merge means SAME for a labeled distinct relationship. A false split means a
non-SAME decision for a labeled SAME relationship. Wrong SUBTYPE/RELATED direction
remains a decision error even when it does not merge or split a pattern. Unavailable
responses stay in coverage denominators and separately fail execution.

Initial reviewable floors, chosen before capture:

- Extraction and matching case pass rate: at least 75% **in each partition and overall**.
- Extraction precision/recall: at least 80% in each partition and overall.
- Geography, duplicates/groups, independence, confidence, scheduler, negative extraction,
  absent-date preservation, sensitivity policy, no false merges/splits and stored stance:
  all sampled checks must pass.

These small-corpus floors are operational regression criteria, not calibrated reliability
estimates. The baseline comparison additionally rejects **every newly failed case check**
and every decreased aggregate metric, even if another improvement masks it in overall
accuracy. Both quality floors and non-regression must pass. Empty denominators are
unknown, not successful. Missing/duplicate/unknown cases, changed input or partition,
forged extraction/semantic checks, altered baseline scorecards and relabeled corpora
fail visibly rather than silently rebasing the baseline.

Exit codes: 0 passes, 1 quality/regression failure, 2 execution/recording/configuration
failure. Execution failures replace stale output with an explicit failed report and
do not print raw exception strings, source content or credentials.

## Capturing a proposed prompt/model change

Replay checks the corpus hash, conservative semantic-source hash and each exact
prompt/schema request fingerprint. Missing or stale responses never fall back to a
provider. All recorded entries must be consumed exactly. A hashed semantic-source edit,
including a nonfunctional edit, conservatively requires an explicit reviewed recapture.
Deterministic policy changes are recomputed from current code and compared to labels.

With `SARVAM_API_KEY` already in ignored `.env`, explicitly capture a candidate:

```sh
uv run --locked python -m benchmarks.evaluate --mode capture \
  --recording .tools/evaluation/candidate-recording.json \
  --output .tools/evaluation/candidate-report.json \
  --baseline benchmarks/data/36/baseline.json --max-calls 20
```

Capture is serial, bounded to the supplied call cap (maximum 40) and a 25-second provider
timeout per call. It refuses to overwrite an existing recording without `--overwrite`.
It writes recordings atomically after cases and on failure, so partial captures remain
diagnosable; a partial recording is not a passing baseline. No model output becomes a
label. Preserve the candidate report and replay it with the candidate recording before
proposing any baseline replacement.

Future prompt/model/scheduler changes should include this report and retain existing
integration/safety tests. Review labels, source changes, per-case errors and thresholds
before accepting a new baseline. Corpus changes require an explicit reviewed new
baseline; do not edit labels to fit observed responses. Replays cannot detect silent
vendor model updates; a fresh capture is needed to assess current provider behavior.

## Measured baseline — 2026-11-22

The bounded capture used **16 Sarvam requests and 15,723 reported tokens**, with complete
usage metadata. All subsequent runs were offline replay. The committed recording holds
synthetic completions only; request IDs and credentials are not exported. Replay
reports identify the original capture time and source hashes.

| Task | Passed |
| --- | ---: |
| Extraction / geography | 8/8 each |
| Matching decision + target + stance | 8/9 |
| Stored attachment stance | 9/9 |
| False merges / false splits | 0/7 distinct opportunities; 0/2 SAME opportunities |
| Duplicate pairs / groups | 6/6; 2/2 |
| Independence / confidence / scheduler | 4/4; 5/5; 7/7 |

**Retained failure:** `broader-not-subtype` returned SUBTYPE where the label is RELATED.
It incorrectly treats a broad deceptive-fare claim as narrower than a rigged-meter
candidate. The holdout matching pass rate is therefore 4/5; development is 4/4. The
case remains visibly failed in the baseline, above the preselected aggregate floor.
No prompt, label or application behavior was changed to turn this observation green.

The baseline is a proposed review gate, not proof of worldwide coverage, current scam
prevalence, real web acquisition quality, production latency or complete semantic safety.
Production is untouched. PR 33's actual explorer terminal-rendering gate remains deferred.

Local validation: the full suite passed **1,038 Python tests, 10 skipped**, and
**25 JavaScript tests**. Ruff lint/format and the exact offline CI baseline command
passed. Corpus/model records and the retained mismatch are reviewable before merge.
