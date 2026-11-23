# Deterministic research scheduler (PR 30)

Apply migration **0021** before starting the updated API or worker. It adds anonymous
demand buckets, persisted scheduler cycles, daily reservations, hypothesis research
state and the `hypothesis_research` job kind. Enable proactive scheduling explicitly
with `APP_SCHEDULER='{"enabled":true}'` in ignored `.env`, then restart the worker.
The default is disabled; existing on-demand research continues to work.

The arq worker evaluates the shared `scheduler_cycle(ctx)` function every 15 minutes
(minutes 0/15/30/45, second 5). An AWS scheduled trigger can invoke this same function
later. It reads bounded database candidates and commits jobs/reservations; it never
calls Serper, fetches pages, calls Sarvam, embeds text or crawls destinations itself.

## Demand, tiers and priority

Calls to the shared on-demand research service record one demand event for the
requested persisted location, including fresh-cache reads. Counters are aggregated
by UTC hour, with no visitor identity, IP, incident text or query text. Invalid
locations/requests do not create demand. REST `research=false` reads and polling,
detail/evidence reads and incident matching do not increment these counters.
Future MCP research reads can use the same boundary.

Windows cover approximately 24h, 7d and 30d at hour resolution. Buckets older than
the configured dormancy horizon are pruned in bounded batches even when proactive
scheduling is disabled. Demand measures requests, not distinct travelers.

| Tier | Default eligibility signal | Minimum interval |
| --- | --- | --- |
| HOT | At least 10 requests in 24h or 3 recently observed source reports in 7d | 8 hours |
| ACTIVE | At least 3 requests in 7d or a recently observed source report | 2 days |
| NORMAL | Some demand in 30d | 10 days |
| COLD | Older interest/research within 90 days | No proactive refresh |
| DORMANT | No current demand/activity and no recent history | User-triggered only |

New hypotheses and locations needing their first deep pass can be eligible without
a preceding full-research timestamp. Once attempted, cooldown and retry rules apply.
Routine location cooldown uses full/deep research and its latest non-initial job;
hypotheses use their latest scheduled attempt. Tier intervals govern proactive work
independently of the existing on-demand cache freshness setting.

Priority combines capped demand, staleness relative to the tier interval,
uncertainty/contradiction hints, recent activity, incomplete research, first deep
enrichment and new hypotheses. Weights and thresholds are configurable. Equal
priorities use kind then UUID, so selection is deterministic for the same captured
database/time inputs. First deep passes, evidence bursts and uncertain/new hypotheses
can outrank routine refreshes. This is a heuristic for allocating research effort,
not a public risk/confidence score and not machine learning.

Candidates come from locations with demand/activity and current eligible hypothesis
hints, rather than every place in the world. At most 200 location and 200 hypothesis
candidates are inspected by default. Candidate preordering favors current demand
and activity; truncation sets `coverage_complete=false`. Selection uses cached
evidence grades and lightweight current metadata checks, without full-document
hashing or recomputing scores. Raw source counts can prioritize investigation but
never increase public corroboration. Workers verify full public eligibility before
hypothesis discovery.

## Two worker job types

- `location_research`: shared bounded deep enrichment or refresh. Pending location
  research is reused rather than replaced. A complete first deep pass establishes
  initial and enriched completion; scheduled work never creates an unbudgeted initial
  continuation.
- `hypothesis_research`: targeted discovery using the hypothesis's public supporting
  summary plus its actual resolved location path. Internal canonical descriptions
  and held associations are not sent as search topics. A no-longer-public hypothesis
  is skipped before provider calls.

Both reuse the same acquisition, normalized-document processing, relevance,
extraction, resolution, evidence, scoring and emerging-review pipeline. Targeted
discovery does not force new claims onto the selected hypothesis: the normal resolver
can attach, create a separate hypothesis or request review. Both retain existing
job leases, fencing, stage checkpoints, partial results and maximum three attempts.
No extra prose-generation model is called.

Only one proactive target per location is admitted in a cycle, and active research
at that location blocks another proactive job. Explicit on-demand location work
remains separate and may take precedence; these budgets apply to proactive work,
not reports, manual legacy research or on-demand requests.

## Persisted budgets and recovery

Each admitted job stores immutable `ResearchLimits` in its payload. Reservation is
pessimistic: **three times** its query, fetch-operation and LLM-call bounds, plus one
job, covering every permitted worker attempt. Changing settings cannot enlarge an
already admitted job's limits. Defaults are:

| Budget | Jobs | Search queries | Fetch operations | LLM calls |
| --- | ---: | ---: | ---: | ---: |
| Per 15-minute cycle | 2 | 36 | 120 | 144 |
| Per UTC day | 20 | 360 | 1200 | 1440 |

Fetch units are bounded document-fetch operations; each transport operation retains
its existing bounded retries/redirects. These are resource ceilings, not currency
or token estimates. Monetary caps remain deferred. Missing/partial usage and unused
reservations are not refunded, so failures cannot silently create more capacity.

An advisory transaction lock serializes budget admission and UTC-day renewal.
Committed cycle records deduplicate concurrent/multiple-worker ticks in the same
15-minute UTC slot. Jobs, daily charges and reservations commit together; rollback
does not consume capacity or leave a queued half-reservation.

Workers check the daily reservation before taking scheduled work and again before
each provider search, document fetch or LLM call, with a fresh lease-token check
before the external call. A carryover job must reserve its
full bound for the new UTC day, once, before continuing calls. If capacity is absent,
it waits/retries with completed checkpoints retained. No budget is consumed twice
for the same job/day, including concurrent deliveries. Calls already in flight can
finish; request budgets cover calls when started rather than billing settlement.

Redis dispatch happens after the database commit. Redis failure leaves the durable
outbox intact; the existing 15-second redispatch loop now includes hypothesis jobs.
A replayed cycle does not create more jobs/reservations; the outbox loop repairs
delivery. Disabling the scheduler also pauses admitted scheduled work before its
next guarded call, while on-demand work remains available.

## Configuration example

`APP_SCHEDULER` accepts one JSON object, with unspecified values retaining defaults:

```dotenv
APP_SCHEDULER='{"enabled":true,"hot_seconds":28800,"active_seconds":172800,"normal_seconds":864000,"candidate_limit":200,"cycle":{"jobs":2,"queries":36,"fetches":120,"llm_calls":144},"daily":{"jobs":20,"queries":360,"fetches":1200,"llm_calls":1440},"weights":{"first_deep":60,"uncertainty":15}}'
```

The existing `APP_RESEARCH_MAX_*` settings determine the per-job deep limits, then
the scheduler checks whether their full reservation fits every budget dimension.
A job that does not fit is deferred; its limits are not silently reduced. Cron work
does not automatically run a cycle at startup, though pending jobs are redispatched.
The scheduler's internal result lists selected target/job IDs, tiers, priorities,
resources, coverage and queue status; public intelligence contracts stay unchanged.

Migration downgrade refuses to proceed while hypothesis jobs exist or proactive work
is pending. Stop workers and deliberately archive/drain that work before reverting;
the migration does not delete job history to fit the older job-kind constraint.
