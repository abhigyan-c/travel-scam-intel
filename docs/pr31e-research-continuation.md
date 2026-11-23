# PR 31E: bounded durable research continuation

Base/target: `develop` at `9579f5e`; branch: `codex/pr-31e-research-continuation`.

## Problem and resulting behavior

Previously, each location phase rediscovered and fetched the same prefix of sources.
Classification always considered the first `max_passages` passages, including cached
ones, so a long document could stay incomplete forever. Classification/extraction
checkpointed the whole stage: interrupting a later passage could discard earlier work.

Location research now shares a versioned `continuation-v1` cursor across the initial
job, enrichment, crash retries and at most one additional continuation job. It resumes
saved documents before purchasing additional searches. Completed queries are not
reissued; remaining queries advance through the existing six-query plan. Discovery
still uses bounded concurrency, source diversity and the same English query policy.

The cursor lives in the existing location state's JSON progress. No database migration,
new queue, service, credentials or paid provider is required. Targeted hypothesis jobs
use the same pipeline and save their cursor in the existing job result for crash retries.
Legacy ad-hoc `research` jobs and the CLI remain single invocations without a root cursor.

## Durability and reuse

- Reserve query, logical fetch, eligible-document and LLM-call allowances **before**
  invoking external work. Reservations survive interrupted calls; unknown billing is
  not reported as zero usage. Concurrent discovery reservations serialize in a lock.
- Every cursor write and domain checkpoint revalidates the current job token/lease.
  Lost ownership prevents the external call or rolls back uncommitted domain changes.
  The existing database outbox repairs queue outages; no in-memory-only successor job.
- Commit the immutable document ID/content hash with ingestion. Within this root,
  verify the saved source identity/hash and reuse its processed text only when the
  processing version and recalculated clean-text hash match. A fresh refresh root
  fetches sources again; it may reuse derived results if the returned bytes match.
- Save each completed classification passage. Cached successes do not consume the
  next invocation's new-passage allowance. Same-root classification errors get at
  most two recorded attempts; interrupted calls remain charged even without a result.
- Commit each successful extraction passage, including valid empty extractions.
  Existing model/prompt/text-hash/offset-keyed extraction records are reused without
  consuming the next invocation's new-passage allowance. Failed extractions can retry
  only within the shared call/time/job caps.
- Claim application and embedding have separate committed cursor markers, so a later
  embedding failure does not require repeating completed claim application. Existing
  evidence/extraction idempotency and source/quote grounding checks remain in force.
- Pipeline versions include discovery, acquisition, processing, relevance, extraction,
  location resolution, Sarvam model and embedding model/revision. Version changes
  invalidate derived completion markers but retain source pointers and spent budgets.
  Changed query plans discard obsolete query-cache entries without refunding calls.
  Unknown cursor schemas or changed targets fail closed, rather than resetting budgets.
  Relevance caches now explicitly record the model; legacy model-less entries are
  conservatively reclassified once.

The cursor stores source descriptors/IDs and derived stage summaries, not raw source
bodies or passage text. Passage assessments remain in document metadata. MCP's public
research-status contract continues to expose status fields, not this internal cursor.

## Resource ceilings

Existing per-invocation limits remain 25 seconds initially and 45 seconds for enrichment
with the default settings. Total root allowances are the sum of initial and enrichment
allowances, rather than a new allowance for every retry:

| Resource | Default location root maximum |
| --- | ---: |
| Logical search calls, including retries | 9 (at most 6 distinct planned queries) |
| Logical source fetches | 30 |
| Eligible source documents | 16 |
| LLM completion calls | 36 |
| Automatic phase jobs | 3 |
| Worker attempts per job | 3 |
| Reserved execution time, including crashes | 210 seconds |

These are ceilings, not targets. Each attempt conservatively reserves its entire time
slice up front; a crash cannot restart that clock. Native fetch transport retries and
redirects remain bounded by the existing fetcher and its 6-second cap; logical fetch
counts are not a count of individual HTTP exchanges. Limits use the settings captured
when the cursor is created; changed runtime settings cannot refund an existing root.

An extra continuation requires remaining work, remaining reserved time, fewer than
three phase jobs, and saved document work with newly spent allowance. All-provider
failures do not create another automatic continuation after initial/enrichment.
Sources deferred by same-root host feedback and review-required claims are not an
invitation to keep retrying or approve allegations.

Refresh roots have one phase's resource allowance and at most three time slices.
Scheduled location/hypothesis roots also have one phase's allowance. Scheduled work
does **not** create extra automatic phase jobs: its existing daily admission guard and
at-most-three crash attempts remain authoritative. A scheduler-created first-deep run
starts a new root, rather than inheriting exhausted on-demand progress.

Budget exhaustion, filtered/unusable-only search responses, incomplete passages,
source failures and unresolved/review-held claims remain incomplete. Only completed
negative research may say “Nothing was found.” Continuation never bypasses moderation.

## Evidence

`benchmarks/data/31e/continuation.json` compares the actual classifier from develop
`9579f5e` with this implementation on five synthetic negative passages, allowing two
new passages per invocation and running three invocations:

| Measurement | Develop | PR 31E |
| --- | --- | --- |
| Retained passages after each invocation | 2, 2, 2 | 2, 4, 5 |
| New calls per invocation | 2, 0, 0 | 2, 2, 1 |
| Complete after three invocations | No | Yes |

The legacy run costs fewer calls because it stalls, not because it completes more
efficiently. The artifact includes measured local wall time with artificial 10ms call
latency and source hashes. It does not measure live Sarvam speed or VPS throughput.

Integration tests interrupt a later classification/extraction call and recreate the
worker. They verify one HTTP fetch and one raw-object read across retry/enrichment,
exactly one completed call per passage plus the interrupted call, retained empty
extractions, advancement beyond six passages, and no duplicate evidence. Other checks
cover fresh-root refetch, concurrent reservations, stale fencing, unsupported cursors,
three-job completion and three-job exhaustion that remains incomplete.

Validation: **924 passed, 10 skipped** with infrastructure tests enabled; Ruff and the
production Docker image build passed. Skipped optional live-provider/embedding tests
are not counted as live acceptance.

`benchmarks/data/31e/extraction-replay.json` replays the unchanged 31C Sarvam recording
and frozen 15-case corpus. Positive claim coverage remains **7/9**, geography **5/7**,
eligible public patterns **4/6**; negative safety **6/6**, withholding **9/9**. Cold:
22 recorded attempts/8,578 reported tokens with incomplete historical usage; warm:
1 retry/251 reported tokens. No new provider requests were made. The artifact's commit
identifies the checkout base; it was generated from this PR's working tree before commit.

Reproduce with:

```powershell
.venv/Scripts/python.exe -m benchmarks.continuation
.venv/Scripts/python.exe -m benchmarks.research --mode replay --output .tools/31e-replay.json
$env:RUN_INFRA_TESTS='1'
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/ruff.exe check .
```

## Deployment and remaining acceptance

This PR prepares code for review; it does not deploy. Deploy a matching API/worker image
and replace old workers before running the new cursor: an older worker does not preserve
this cursor or its root accounting. Existing jobs without a cursor initialize one on
their next new-code attempt; already-spent work before that initialization cannot be
reconstructed and is not claimed as accounted usage.

PR 31D's public-source access restrictions and PR 31C's unmet positive quality targets
remain. No new live-city success, live speed improvement or ChatGPT acceptance is
claimed. PR 31F still owns the deployed controlled/live trace and real ChatGPT retest.
