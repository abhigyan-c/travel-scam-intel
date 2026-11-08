# PR 31G: repair the controlled quality gate before PR 32

User-authorized on 2026-11-08 after choosing quality repair before PR 32.
Branch `codex/pr-31g-research-quality-repair`, base/target `develop` at `fc936c4`.
This is independent of the unmerged PR 31F review branch; no stacked branch or
PR 32 implementation is included. PR 31F's historical failure artifacts stay unchanged.

## Investigation and selected repair

The existing relevance/extraction stages can disagree: a relevant passage may receive
a valid empty extraction and be cached as finished forever. A seven-call development
screen tested one independent source audit, preserving an empty answer as valid.
It recovered four of five development positives, missed the landmark, and invented
no claims on the two negatives. The fixed protocol then retained all four positive
holdout claims and both negative controls in six calls. These are diagnostic screens,
not proof of complete end-to-end coverage or a new independent holdout dataset.

The first full trial (`claims-v4`) did not pass: claims 9/9, geography 6/7, public
patterns 3/6. It showed literal anonymous roles such as `driver` and `seller` in the
named-entity array, causing unnecessary holds. It also supplied `In Rome` as a
source-exact location that did not resolve to `Rome`.

The next trial (`claims-v5`) rejected those invalid roles and added source-prefix
resolution, but generic audit instructions sometimes repeated the role mistake.
It scored 7/9 claims, 6/7 geography and 5/6 public patterns. Both failures are retained
with their recordings; neither is relabeled as a passing run.

Selected `claims-v6` adds the entity field's explicit schema description and fixed,
targeted contract feedback when an anonymous role caused rejection. The audit still
allows no claim, must copy supported evidence/fields, retains named entities and
sensitivity rules, and cannot perform actions or obey source instructions. The
initial extraction prompt otherwise remains unchanged. No case text, label, split,
denominator, confidence threshold or public eligibility requirement was adjusted.

## Runtime behavior

- On a relevance-positive passage, a valid nonempty extraction follows the normal
  path. A valid sensitive result is held immediately; it is **not** audited to clear
  its flag. Existing held claims are never approved or rewritten by this repair.
- A valid empty extraction, or rejection for an exact known anonymous-role value in
  `entities`, receives at most one source audit per passage attempt. Unsupported
  quotes/copied fields and other failures do not enter this correction route.
  The narrow role check is a conservative backstop, not general named-entity recognition.
  It never silently deletes an entity or flips a sensitivity flag.
- Both calls consume the existing limited client and cumulative root allowance.
  No query, fetch, model, phase/time, retry or job ceiling increases. A later retry
  may resume a failed audit, but is still subject to those shared caps/deadlines.
- Persist a version/model/text-hash/offset-keyed pending-audit marker in document
  metadata before the audit call, through the current fenced checkpoint. Crashes
  resume the audit without repeating its saved initial response. Failed audits stay
  incomplete and do not create a cached successful negative.
- Successful audit output uses the same strict grounding/schema checks. Store only
  the final validated extraction/claims; confirmed empty outputs remain cacheable.
  Persisted extraction usage includes initial and selected audit usage; failed-call
  history remains in job diagnostics. Warm reuse makes no extra model calls.
- New extraction of overlapping previously held evidence inherits that hold even
  if a newer response omits sensitivity/entities. This prevents prompt upgrades
  from republishing a held allegation as a new claim. Overlap uses matching clean
  hashes/quote offsets or retained quote containment; it may conservatively hold
  a broad overlapping excerpt until review. It does not bulk-reprocess old claims.

`location-v3` recognizes a leading `in`/`at` only when the location field is itself
grounded in retained source text with valid lineage. Resolve the remaining literal
place using the same hierarchy/context rules; retain the original field and component
in resolution metadata. No country/city comes from the search label. Unknown,
conflicting, ungrounded and relative places remain unresolved. No new coordinates,
country roots or automatic moderation approvals are introduced.

Versioned cache keys preserve older extraction records and invalidate their reuse
for this new protocol. Root continuation retains spent reservations on version
change. There is no schema migration, new dependency/service, API key or public
contract change. The stale-status and no-reprompt plans remain future PR 32/33 work.

## Frozen result and cost

The selected capture and replay under `benchmarks/data/31g/` both score:

| Metric | Required | Cold | Warm |
| --- | --- | --- | --- |
| Grounded positive claims | ≥8/9 | 9/9 | 9/9 |
| Correct resolvable geography | 7/7 | 7/7 | 7/7 |
| Eligible public patterns | ≥5/6 | 5/6 | 5/6 |
| Negative safety | 6/6 | 6/6 | 6/6 |
| Withholding safety | 9/9 | 9/9 | 9/9 |

Chiang Mai remains held by a valid sensitivity flag in this capture, and the named
business remains held. Missing/conflicting geography remains unresolved. Blocked and
empty acquisition remains incomplete, not successful negative research. Every
retained claim lineage check passes.

The selected capture used **26 completion calls / 12,413 reported tokens**, with
complete reported usage. Warm runs used **zero** additional calls/tokens. The two
unsuccessful full trials used 22 and 29 total calls, and development/holdout screens
used 13: **90 real synthetic-passage calls across this investigation**, not just the
26 selected calls. Each capture had a 40-call ceiling. No Serper or production R2
writes/research were performed; generated local schemas were removed.

This consumes more model work than a single unchecked extraction and may leave
fewer passages within the same real root budget. No cheaper/faster live result is
claimed. The tiny, now-visible corpus and sequential protocol trials are not an
independent statistical reliability estimate; the role repair and source-prefix
changes were motivated by recorded contract failures, not changed labels. A passing
single capture plus deterministic replay does not promise all stochastic future runs pass.

## Reproduction and remaining gates

```powershell
# Current default is the selected v6 recording; no provider calls in replay.
.venv/Scripts/python.exe -m benchmarks.research --mode replay --output .tools/31g-replay.json
$env:RUN_INFRA_TESTS='1'
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/ruff.exe check .
```

Artifacts retain both unsuccessful pipeline captures/recordings, the development
and holdout screen observations, and the selected live/replay pair. Their commit
field identifies the checkout base; the selected pipeline ran this PR's working
tree. Earlier experimental protocols are historical observations, not recordings
to feed into the selected prompt/schema. Exact selected replay compares case-level
outcomes, errors, completeness, counts and token use, not historical provider time
with local replay wall time.

Tests cover conservative entity rejection, sensitive/named holds, inherited holds
across prompt upgrades, unchanged strict grounding, valid-empty caching, failed-audit
resumption, cancellation in a later audit, no repeat fetch/raw read, and grounded
prefix resolution without search-target leakage. Existing lease/budget tests remain.

Validation on this independent develop-based branch: **932 passed, 10 skipped**;
Ruff and the production image build passed. The selected no-network replay matches
all measured case outcomes, errors, completeness, calls and tokens exactly.

**Controlled quality gate passes. Deployment/real-host reacceptance of this repair is
pending review and merge.** Production remains on merged PR 31E (`fc936c4`), and the
successful PR 31F ChatGPT test belongs to that older release. Do not relabel its host
evidence as testing `claims-v6`. Merge the prerequisite review branches, deploy the
repaired release, then repeat bounded live provenance and real ChatGPT checks before
declaring PR 32 unblocked. Stop after this repair PR; PR 32 has not begun.
