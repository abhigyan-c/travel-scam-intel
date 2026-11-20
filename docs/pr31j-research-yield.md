# PR 31J: reserve capacity for public claim resolution

Branch `codex/pr-31j-research-yield`, base/target merged develop `8b6800f`.
The user retained the live-yield prerequisite on 2026-11-20; PR 32 stays blocked.

## Production evidence

Merged PR 31I was deployed as `8b6800fd78239b3d09a8a1415fd2951d7db0e6da`.
API/worker readiness passed. One bounded Bangkok refresh, root
`b35c02be-680c-4f19-8d95-045140316c5f`, ended partial after approximately 56 seconds:
six query reservations, twelve fetch reservations, four document reservations and
all 24 model-call reservations. The single old Grand Palace card remained public;
no new root-linked lifecycle publication was observed.

Read-only retained diagnostics exposed an ordinary v7 claim with a valid source quote,
resolved Bangkok location, no entities and no sensitivity hold. Its two comparator
attempts both returned `run_llm_limit` with **zero provider requests**. The outer
resolver called that `semantic_provider_failed`, but there was no model verdict to
inspect: upstream processing had spent the root allowance before comparison.

Other retained claims still had missing locations or holds, and a source heading
about avoiding crowds had been extracted as an allegation. Those are separate
quality limitations; neither location guessing nor clearing holds is part of this fix.

## Allocation change

For integrated research phases with at least three calls available in their declared
phase allowance, relevance leaves two calls for extraction/comparison and extraction
leaves one for comparison. The guard checks both remaining phase capacity and the
cumulative root capacity, under the shared model-call semaphore. A denied upstream
call returns `pipeline_headroom`, requests zero provider calls and consumes zero
reservations. Comparators use the original shared limiter and can use that capacity.

This is a floor on remaining upstream capacity, not a guaranteed call for every claim.
A phase can still stop partial because of resource, source, provider, grounding or
review failures. Tiny one/two-call diagnostic phases retain their previous best-effort
behavior; a three-call classification/extraction/comparison path cannot fit there.
Extraction also leaves half its remaining phase time for resolution/application.
Standalone extraction-only research retains its existing allocation and timing.

Capacity denial is not a failed provider attempt. Relevance does not persist a new
failed assessment or increment a passage's retry count when the limiter denied a
zero-request call. Previously completed assessments remain reusable.

The continuation policy includes `allocation=pipeline-v2`. Reconciliation invalidates
derived markers when that policy changes while preserving resource/time reservations.
There are no higher limits, new models, migrations, publication-policy changes,
automatic approvals or budget refunds. Existing lease fencing and cancellation apply.

## Validation and remaining gate

A populated-location integration case uses a long source and a four-call phase:
two relevance calls, one extraction and one semantic comparison produce a second
public candidate with the existing disclaimer. Remaining passages keep coverage
incomplete. Without downstream headroom, a second extraction takes that final call.
Additional tests cover concurrent reservation guards, root/phase limit differences,
zero-request retry semantics and the existing exhaustion/continuation behavior.

Validation: **965 passed, 10 skipped**; Ruff lint/format and whitespace checks passed;
production image built. The unchanged frozen quality replay retained its prior
case outcomes, 26 cold calls / 12,413 reported tokens and zero warm calls. These are
replayed historical provider responses, not new paid inference. No new paid model
experiment, report submission or held-record approval was performed for PR 31J.

The source/model quality issues listed above remain open. This PR repairs a measured
allocation failure; it does not prove adequate live yield. After merge, deploy matching
API/worker images and run one bounded populated-destination refresh. Verify new
root-linked public evidence, actual location and disclaimers in the real ChatGPT flow.
Do not count the old Grand Palace finding or relabel a terminal partial as complete.
PR 32 has not begun.

## Live acceptance on 2026-11-20

Merged release `dc97d12e7b0fd113bfc3c2242283ab36a559ebe5` is deployed. API and
worker use that exact image tag and are healthy; readiness checks passed for
PostgreSQL, Redis and storage. The existing filtered MCP hostname is unchanged.

One bounded Bangkok refresh, root `acd38567-2e04-49aa-8acd-018d6b4653df`, retained
a new public taxi meter/fare-negotiation candidate at Bangkok city scope:
`6693cba7-31f7-4b73-a5ed-2d087eeee881`. The root-linked `hypothesis-v4` NEW lifecycle
event was created during this run, with no review approval, candidate status, limited
confidence and the public uncertainty disclaimer. Bangkok's public count rose from
one to two. The old Grand Palace bird-feeding finding is retained separately and
was not counted as new growth.

The new evidence cites Your Thai Guide. Raw-object hash, clean-text hash, quote
offsets, source/claim lineage, publication eligibility and actual location checks all
passed. These are provenance checks, not verification that an incident occurred.
Provenance classification is UNKNOWN, occurrence/publication dates are absent and
the source is a single public-web allegation; those limits remain public.

The root stopped partial after approximately 79 seconds across three phase jobs.
Reservations: six queries, twenty fetches, ten documents and 23/24 model calls.
The final two-second continuation used zero model calls; this is a remaining
continuation-efficiency limitation, not a claim that all phase scheduling is optimal.
There was no budget extension or automatic restart. Legacy reads label the stopped
partial `stale` with no successful-completion/freshness timestamps; PR 32 still owns
that misleading state contract. Three read-only local REST observations were
approximately 0.16–0.28 seconds while the run was active, independent of connector
and relay overhead.

Sanitized run/publication artifacts are in `benchmarks/data/31j/`. Backend publication,
provenance and bounded-termination checks passed. **Real ChatGPT presentation acceptance
is pending**: the user has been asked to read both patterns without requesting research,
fetch evidence, preserve city versus Grand Palace scope, and explain candidate confidence,
missing dates and incomplete coverage. PR 32 remains gated on that response; do not
claim complete conversational acceptance from connector calls alone.
