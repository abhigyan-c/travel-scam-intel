# On-demand research (PR 24)

`get_location_intelligence(ctx, location_id)` reads persisted intelligence and coordinates
bounded research. It takes a resolved location UUID; existing location resolution handles
names, ambiguity and provisional records with trusted geographic context. The operator
CLI accepts an existing unambiguous name. An unknown name is not silently turned into
an invented country or guessed city.

```powershell
uv run python -m app.research_on_demand Fiji
uv run python -m app.research_on_demand Fiji --no-wait
```

Apply migration 0017 first. This is a service/operator entry point; PR 27/28/31 define
the final query, public REST and MCP contracts. Full `progress` objects are internal and
contain provenance IDs and acquisition URLs, including records waiting for review.

## State and request behavior

`LocationResearchState` is a single PostgreSQL row per resolved location. It stores
`never_researched`, `researching`, `ready`, `stale` or `failed`, the active durable job,
lease token/deadline, initial/enriched timestamps, last successful run, freshness expiry,
retry cooldown and progress. A missing row reads as `never_researched` without starting
work. `read_location_intelligence` never initiates research.

- Fresh READY: return stored cards without provider calls. Freshness defaults to 24 hours
  after a complete bounded run and is independent of hypothesis confidence.
- Stale: return stored cards immediately and reserve one background refresh. Partial or
  failed work has a default five-minute cooldown before another request can retry it.
- Never researched: reserve one initial job and run it within the interactive budget,
  or return immediately with `wait_for_initial=False`. Other callers share the job and
  receive persisted partial cards plus in-progress metadata.
- Initial attempt finished: retain completed stages, return initial results and reserve
  exactly one enrichment job. Even an initial provider failure can receive this bounded
  background follow-up. Further failures are throttled rather than looping indefinitely.
- Enrichment/refresh complete: mark READY, including successful empty findings. Errors
  and review-pending results remain incomplete; they become STALE or FAILED with a retry
  cooldown and retain prior cards. They are never converted into a successful empty search.

The result exposes `initial`, `enriched`, `stale`, `in_progress`, `complete`, active job ID
and queue-pending state. `initial` means an initial attempt returned; it does not assert
success. `enriched` means a deeper run completed successfully. Country queries may return
cards at descendant locations, always labeled with their actual `primary_location_id`.
No ancestor fallback or sibling widening is introduced. Only current hypotheses with
eligible supporting evidence enter these minimal cards. Named-actor/sensitivity holds
and linked-report moderation are checked again on reads. Card scores are caches from
pipeline scoring; PR 27 will finalize current-score and evidence response contracts.

## One pipeline and bounded work

The service reuses `research_location`: Serper discovery → immutable raw ingestion →
English processing → Sarvam relevance/extraction → geographic resolution. Optional hooks
then call the existing hypothesis resolver, lifecycle application, confidence and frequency
helpers, and scoped embedding backfill. There is no second extraction or scoring pipeline.

Only eligible deterministic resolutions are applied automatically. Semantic matches,
ambiguous geography, contradictions and sensitive allegations retain their existing review
requirements. The service never manufactures an operator review. Such records remain
inspectable while ordinary deterministic candidates can return with low-confidence labels.

Completed stages commit independently, after validating ownership of both the research
job token and the location lease. Hypothesis application/scoring commits before local
embedding work, so loading a model cannot discard completed intelligence. A cancelled
or failed stage rolls back only unfinished work. Later runs rediscover within the bounded
search budget and reuse the persisted raw/document/extraction/resolution records; they
do not blindly create duplicate claims, hypotheses or evidence. Retrieval still fetches
the source to detect new versions. Deterministic object keys make a storage write safe
to retry if its database transaction was cancelled.

Configuration (`.env`, propagated to API and worker by Compose):

| Setting | Initial default | Maximum |
| --- | ---: | ---: |
| `APP_RESEARCH_MAX_SEARCH_QUERIES` | 3 | 6 |
| `APP_RESEARCH_MAX_SEARCH_RESULTS` | 30 | 100 |
| `APP_RESEARCH_MAX_DOCUMENTS_TO_FETCH` | 10 | 20 |
| `APP_RESEARCH_MAX_RELEVANT_DOCUMENTS` | 6 | 10 |
| `APP_RESEARCH_MAX_LLM_CALLS` | 12 | 24 |
| `APP_RESEARCH_MAX_LLM_CONCURRENCY` | 2 | 4 |
| `APP_RESEARCH_MAX_TOTAL_SECONDS` | 25 | 45 |

Enrichment/refresh doubles initial query/result/fetch/document/call budgets, capped at
those maxima; time doubles up to 45 seconds. The relevant-document bound is conservative:
it counts English documents admitted to relevance/extraction, including ones later judged
irrelevant. The shared LLM call counter and semaphore include resolver calls. Stage
execution/fetching remains serial in this PR; the concurrency setting is an upper bound,
not a promise of parallel throughput. Local embedding work is bounded to this pipeline's
hypotheses and uses the pinned model cache. Missing model files produce explicit partial
results; online model downloads never occur during requests.

The elapsed budget bounds pipeline work, with remaining-time checks at stage boundaries
and an outer timeout. Short reservation/finalization/queue/read operations add overhead;
this is not a guaranteed end-to-end transport SLA. A hard timeout can interrupt accounting:
the partial result marks `usage_complete=false` rather than claiming zero provider usage.
Successful stage checkpoints remain available even when no final claim list was returned.

## Durable delivery and recovery

`ResearchJob(kind=location_research)` is the database outbox. Creation and the location
reservation commit together before contacting Redis or providers. Redis delivery uses the
existing idempotent job IDs. A Redis outage returns `queue_pending=true` and leaves the
job committed. Worker startup and a 15-second location-job dispatch sweep repair missed
delivery. The sweep also redelivers expired jobs; ordinary ingest/process/research job
behavior is preserved.

Runs have 90-second leases, longer than the largest 45-second pipeline budget. Each
attempt obtains a new token. Checkpoints and finalization lock/check both job and state,
so an expired or superseded worker cannot commit domain writes. Stale transactional
writes roll back; immutable raw objects can be reused by the new owner. There are at
most three worker attempts per job, followed by FAILED/cooldown. Cancellation leaves a
retryable durable job. Successful initial finalization and enrichment-job creation are
atomic, including when the caller disconnects after the database commit.

Compose mounts `.tools/embedding-cache` read-only at `/models`. Preload the pinned local
model through the existing embedding tooling before expecting semantic retrieval to
complete; provider credentials remain in the ignored `.env` (`SERPER_API_KEY` and
`SARVAM_API_KEY`). No keys are stored in job payloads or progress records.

Migration 0017 adds the research-state table and extends the job-kind constraint. Its
downgrade refuses to truncate the schema while location jobs remain; operators must
explicitly preserve/archive that work before reverting.

## Validation

Isolated PostgreSQL-schema tests exercise the real persistence, processing, extraction,
resolver, evidence and scoring stages with controlled provider/storage boundaries. They
cover first-query concurrency, partial deadlines, slow embedding load, cancellation,
expired-token fencing, exhaustion, retry/continuation, Redis recovery, fresh/stale paths,
empty vs failed research, and sensitive/semantic review holds. Test schemas are generated
per case and removed without touching public/user data.

PR 24's review gate additionally requires a live, previously unresearched-location run
and inspection of persisted provenance and state. Its outcome is recorded with the PR.

### Live review: Fiji, 2026-10-29

Preflight confirmed Fiji had no `LocationResearchState`. The real Serper/Sarvam initial
run returned in 25.062 seconds, retained 14 successful stage checkpoints and queued one
enrichment job. The real worker consumed enrichment once and finished with partial
results after 38 stage checkpoints. Final state was STALE, `complete=false`, with a retry
cooldown and zero public cards; the service did not label this a successful empty search.

Database/object inspection verified 19 newly persisted source documents and all 19 raw
SHA-256 hashes. Five documents were eligible for English extraction. Multiple social/
forum pages were empty or inaccessible; model responses on usable pages included fields
that failed the existing strict grounding checks (`invalid_grounding`). No new claims or
hypotheses were accepted. No grounding, review or publication rule was weakened to obtain
findings. This live run verifies partial retention, continuation, provenance and honest
failure reporting; it does **not** establish live extraction quality or scam corroboration.
The controlled end-to-end tests separately verify successful claim → evidence → scoring.

Final automated validation: 654 Linux tests passed (8 opt-in live-provider tests skipped),
653 Windows tests passed (the same 8 plus one Unix-only worker test skipped), formatting,
lint and Alembic schema check passed. Migration 0017's empty-state downgrade/upgrade
cycle was checked before the live records were created. Review the partial live outcome
before treating extraction coverage as production-ready.
