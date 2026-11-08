# PR 31I: bounded grounding correction

Branch `codex/pr-31i-grounded-extraction`, based on merged develop `ab051fa`.
See [failed live acceptance](pr31h-live-reacceptance.md). This repair does not
declare PR 32 unblocked or alter the publication policy.

## Diagnosis and change

Read-only inspection of the retained Pattaya root found repeated `invalid_grounding`
errors in approach, methods and warning signs, sometimes location/entities too.
The model paraphrased fields whose contract requires exact quote substrings.
Continuations retried the same initial request without knowing which fields failed.

The three retained claims were different cases: a named-business allegation explicitly
in Bangkok (outside the Pattaya request context); a sensitive role allegation with
no quoted location; and a sensitive allegation naming “Pattaya Beach or Jomtien”.
None warrants replacing its geography with the search destination or clearing its
hold. Location rules and existing holds are unchanged by this PR.

`claims-v7` adds one correction after a locally classified grounding failure. Only
fixed field names go into feedback, never rejected values or instructions from the
source. The correction schema requires empty approach/methods/warning-sign arrays;
the claim summary and exact evidence quote still describe the alleged mechanism.
This deliberately requests less optional structure; it does not strip invalid model
output after validation. Location, entities, dates and quotes keep their exact-source
checks. Unsupported source geography still fails, and missing geography stays missing.

Persist the first failure before the correction call, using the existing version/hash/
passage checkpoint. Provider failures or cancellation resume that correction instead
of repeating the initial extraction. A second locally invalid result is cached as a
failed passage: future continuations return incomplete with zero new calls for it.
Other passages remain eligible. Cache entries change with text/model/prompt version.
An unclassified provider error is not made permanently invalid.

Preserve a hold indicated by sensitivity or entities on the initial grounding failure,
even when a correction omits them. Existing anonymous-role audit behavior remains
separate. Valid sensitive outputs are never audited just to clear their holds; older
held overlapping evidence also retains the existing inherited hold.

No extra root allowances, provider, migration, automatic approval or search-city
fallback is introduced. A correction can spend a model reservation sooner than the
old initial-only path; it remains inside existing cumulative caps. An unrecoverable
passage cannot consume the same work again on each continuation.

## Experiments and limits

Two sequential development screens used the first 4,000 retained source characters
from Siam Legal, Your Thai Guide and Toast to Thailand. They made no production writes,
queue requests or incident submissions. This small, now-visible sample is not an
independent acceptance benchmark.

1. Feedback alone: six calls, 10,980 reported tokens. All three first requests and
   all three corrections failed grounding. This protocol was rejected.
2. Constrained correction: six calls, 10,659 reported tokens. All three first requests
   failed. Corrections returned one grounded Bangkok claim, one valid empty output,
   and one remaining invalid location. Counts/field failures/provider usage are retained
   in `benchmarks/data/31i/constrained-screen.json`. A valid empty output is not counted
   as recovered claim yield; a grounded claim is not automatically a public pattern.

Total investigation: 12 calls, 21,639 reported tokens, including the failed approach.
No further screen was sampled to obtain a perfect result. The remaining location
failure is retained honestly. The selected short corpus replay has unchanged outcomes
and 26 cold / zero warm calls; it does not exercise these new real-article failures.

Tests cover failed-pass caching, correction retry/checkpoints, ordinary versus held
claims, exact retained lineage, provider-error retry compatibility and rejection of
nonempty correction fields. Schema construction rejects arbitrary feedback strings.

After merge, deploy matching API/worker images and run one bounded populated-location
check. Verify new root-linked public evidence and disclaimers, and inspect retained
failures without approving them. The real publication/ChatGPT gate remains pending;
production still runs merged PR 31H. PR 32 has not started.

Validation: full suite **960 passed, 10 skipped**; final mixed-role/named-entity
hold guard and extraction unit tests **24 passed**. Ruff and diff whitespace checks
passed, and the production image built. The no-network replay retained its prior
case outcomes, 26 cold calls / 12,413 reported tokens and zero warm calls.
