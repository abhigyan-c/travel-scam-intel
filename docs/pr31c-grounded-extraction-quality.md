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

The frozen v3 pipeline baseline and complete validation results follow after capture.
