# PR 31C — Grounded extraction quality

Branch `codex/pr-31c-grounded-extraction-quality`, base/target latest `develop`
after PR 31B's squash merge `d523f78`. No acquisition, queue, timeout or publication
policy changes are included.

## Investigation and selection

The PR 31A corpus/targets and old recordings remain unchanged. A fresh 21-call
development screen compared v2, a larger instruction rewrite and evidence-first
field order. Both proposed replacements produced a claim on a negative control;
both also had copied-field grounding failures. They were rejected. Fresh v2 itself
found four of five positive development claims, unlike some earlier frozen misses:
temperature zero is not evidence of deterministic provider output.

A smaller addendum retained v2's schema/grounding contract and clarified that
reported deception needs no verified perpetrator, payment or optional details;
unknown fields can remain null/empty. Anonymous roles are not named entities;
implicated named people/businesses still require sensitivity handling. Ordinary
transactions, legitimate closures and source instructions remain negative controls.
It yielded five grounded development positives and zero claims on two negative
controls (seven calls). The selected wording then passed four positive/two negative
reserved holdout passages (six calls), without tuning to those results.

The schema had a separate deterministic defect: only the first 32 *distinct* source
line excerpts could be selected. Repeated identical introductory lines do not expose
this defect because excerpts are deduplicated. An initial repeated-line diagnostic
and coarse long-quote packing trial were inconclusive/unsuccessful and remain saved.
The distinct-line diagnostic reproduces excluded late evidence; the old model output
was rejected because location/approach were outside its allowed introductory quote.
The chosen coverage fix retained a grounded late claim and no claims on the normal-
fare/injection controls (six calls comparing old/new).

All screens, rejected candidates and both article diagnostic sets are saved under
`benchmarks/data/31c/`. These small single-call observations establish regression
cases, not statistical reliability or production latency improvements.

## Implemented contract

- `claims-v3` uses exactly the evaluated minimal addendum. It still requests one
  claim in one call, validates strict field types and exact quote/field substrings,
  and rejects unsupported output rather than repairing or retrying it.
- Allowed quotes cover the entire ≤4,000-character passage. Short line excerpts
  are retained when there are ≤32; otherwise adjacent lines are packed into bounded
  contiguous choices, preserving the tail. Long-line fragments overlap at split
  boundaries. Each choice remains an exact source substring of at most 1,000 chars.
- Grounding failures retain safe fixed field names in extraction attempts under
  `validation_errors`. Rejected text/values are not included. Provider success is
  still distinct from validated extraction success.
- Existing v2 successes/empty extractions remain immutable. Versioned extraction
  keys cause reprocessing under v3; subsequent successful v3 results are reused.
  A valid empty result remains valid/cacheable. No forced positive output or
  automatic retry is introduced.
- Sensitivity flags/entities are not cleared to increase public counts. The
  existing held-claim/public-evidence rules remain authoritative.

No database migration, model change, token-limit increase or additional runtime
provider call is needed. Existing production claims are not bulk-reprocessed or
approved by this PR. Source lineage checks are necessary but do not mathematically
prove semantic entailment of a generated summary; independent controls and human
review remain important.

## Reproduction

The regular pipeline benchmark defaults to the new v3 recording at
`benchmarks/data/31c/sarvam-recording.json`. Old v2 artifacts are preserved for
comparison; their prompt fingerprints must not be replayed as v3 responses.

```powershell
# No external calls: local PostgreSQL and recorded responses.
.venv/Scripts/python.exe -m benchmarks.research --output .tools/31c-replay.json

# Explicit paid diagnostic: use a new output path, bounded calls.
.venv/Scripts/python.exe -m benchmarks.extraction_diagnostics --live `
  --variants baseline minimal --max-calls 14 --output .tools/31c-new-screen.json
```

Diagnostic inputs are original synthetic content, never real accusations, secrets
or production reports. Source acquisition is controlled. Article/short-case model
comparisons do not measure live search, blocked sites, multi-job continuation,
embedding throughput or end-to-end application speed. Those remain 31D–31F work.

## Frozen pipeline result

`benchmarks/data/31c/pipeline-baseline.json` captures the actual research/services
pipeline from commit `84b1ec6` in isolated local schemas; its corpus remains the
original 15 cases. Source acquisition is synthetic. The new recording is separate
from the original v2 provider evidence.

| Metric | Original frozen baseline | New v3 capture |
| --- | ---: | ---: |
| Grounded positive claims | 5/9 | 7/9 |
| Correct scoped geography | 2/7 (3/7 after 31B replay) | 5/7 |
| Eligible public patterns | 2/6 | 4/6 |
| Negative controls without claims | 6/6 | 6/6 |
| Withheld cases without public patterns | 9/9 | 9/9 |

The landmark and named-business cases still returned valid empty extractions in
this capture, although the direct selected-protocol screens extracted them.
Rome's claim was marked sensitive and correctly remained under review. No flag
was cleared to improve the score. A single small stochastic capture is not a causal
estimate of the improvement or a release-quality guarantee; **31F targets remain
unmet**. The named-business pipeline miss does not itself verify live moderation,
although controlled held-claim tests and the direct named-business screen cover it.

Cold execution used 22 model-call attempts and **8,578 reported tokens**. Warm
reprocessing used one additional relevance attempt / 251 tokens after an interrupted
cold relevance call; completed v3 extractions were reused. The interrupted request's
token usage is unknown, so 8,829 is a known reported subtotal, not total billed use.
There were no Serper calls or production writes. Development comparison medians
were 1.640 seconds for v2 and 1.469 for the minimal addendum, but the addendum used
more tokens (4,293 versus 3,151 over seven calls). These small sequential samples
do not establish a production latency reduction or a cheaper cost per request.

Replay exposed an interrupted-call recorder defect: cancellation occurred before
the response append, so the later successful warm response initially replayed as
the cold call. The recording now includes an explicit deadline sentinel before that
retry. Its missing response duration/usage are marked unknown, not fabricated; the
original live result/timing counters remain intact. The `reconciliation` metadata
documents this repair. Recorder cancellation regression tests prevent recurrence;
per-case/summary `usage_complete` and timing flags identify incomplete measurements.
Recorded response duration is not the actual duration of no-network replay.

Final no-network replay matches every live outcome, error, completeness, call and
known-token counter. This PR improves quote coverage and measured usefulness while
retaining strict failure and moderation behavior; acquisition and continuation
remain separate work. It is not deployed and does not begin PR 31D automatically.

Validation: **890 passed, 10 skipped** with infrastructure enabled; Ruff lint and
format checks passed. Coverage includes old-empty cache invalidation, retained v3
cache hits, strict rejection with safe field diagnostics, complete quote inventory,
held-claim behavior, recorded pipeline replay and interrupted-call accounting.
