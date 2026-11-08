# PR 31H: public ordinary candidates and productive continuation

User-authorized policy update, 2026-11-08: let travelers see low-confidence ordinary
findings with a disclaimer instead of putting every semantic decision behind manual
review. Branch `codex/pr-31h-public-candidate-growth`, base/target latest `develop`
after merged 31F/31G (`122b0a8`). PR 32 remains separate.

## Evidence behind the repair

The deployed Bangkok refresh finished in about 68 seconds with 20 fetch reservations
and all 24 model-call reservations consumed. The last continuation used zero model
calls. Two v6 claims were retained: one lacked resolved geography; another received
an unambiguous `NEW` decision at 0.95 confidence but was blocked solely by
`semantic_decision_requires_review`. The only public finding was retained from an
older run. Waiting ten more minutes could not make that stopped root advance.

Inspection also found document-wide terminal flags whenever any claim needed review
or had unresolved geography. That could abandon other unprocessed passages. The
passing short synthetic corpus started with empty hypothesis scopes, so it did not
establish that research could grow a populated destination or finish long articles.

## Publication policy

Ordinary evidence does not need a strong corroboration score to be visible. It can
be presented as an **unverified candidate**, with sources, actual geographic scope,
dates or unknown dates, confidence and report/corroboration limits.

The automatic path now permits an unambiguous `NEW` decision when the claim has a
resolved location, a retained evidence quote, explicit non-sensitive status, no named
entities and ordinary publication eligibility. The resolver's numeric match-confidence
threshold alone does not block that independent candidate. A truncated candidate
retrieval set also does not certify or prevent a provisional new record: similar
reports may appear separately, and no global novelty is asserted. Source eligibility,
schema/quote validation, scoring and public-read checks remain authoritative.

Three different uncertainties must stay distinct:

- Evidence/corroboration confidence: low values remain visible with a warning.
- Resolution confidence: certainty about whether this claim matches an existing
  pattern; it is not the probability that the reported incident happened.
- Research completeness: coverage of a bounded run, independent of either score.

Every candidate card, and any card with limited/unknown/no-support confidence, adds
an uncertainty disclaimer through the existing `metrics.limitations` field. It says
the finding is not a verified incident, prevalence estimate or confirmed new scam,
and matching can be incomplete. REST and MCP share this projection; MCP instructions
explicitly require the disclaimer, provenance and actual scope. No new fields or enum
values are silently added to the strict `intelligence-v1` envelope.

This is not blanket approval of model output:

| Situation | Behavior |
| --- | --- |
| Ordinary, grounded, resolved, unambiguous `NEW`, even with low match confidence | Create/score a separate **candidate** without operator approval; show disclaimer. |
| Model declares `NEW` ambiguous, or location is unresolved | Retain for review/unresolved handling. Do not invent a destination or silently merge. |
| Semantic `SAME`, `RELATED`, `SUBTYPE` or `CONTRADICTS` | Existing review requirements remain; a disclaimer does not authorize changing another pattern or its evidence. |
| Sensitive flag, named entities, existing held status or inherited source hold | Keep held; no public scoring/publication bypass. |
| Unsupported quote/fields, provider failure, malformed decision or invented target | Remain invalid/incomplete; never become a public candidate through this policy. |

Deterministic exact/empty-scope paths retain their existing behavior. Automatic
creation never promotes a node to verified/active, approves an old hold, merges
patterns, resolves contradictions or overwrites an existing node. The lifecycle
writer independently checks the eligibility policy and current input snapshot;
changing a review flag alone cannot bypass semantic-write safeguards.

The resolver policy becomes `hypothesis-v4` and lifecycle audit becomes `lifecycle-v2`.
No old pending decision is bulk-approved. New resolutions use current inputs and
versioned caches; older unexecuted resolution versions cannot be applied silently.
Continuation versions now include both policies, invalidating old derived markers
without refunding previously spent resource reservations.

## Passage and continuation fixes

A blocked claim is cached at claim granularity so later attempts do not keep
reapplying the same unresolved/review-required result. A document becomes terminal
for those reasons only once classification/extraction are complete and every remaining
error is a stable claim-level blocker. Other passages and transient failures remain
resumable. An unresolved first claim can coexist with a later public finding; overall
research still honestly reports incomplete coverage.

Continuation admission now checks whether the cursor can advance with its remaining
allowances. It preserves follow-up for saved claims/embeddings but does not enqueue
another phase merely because unprocessed source URLs exist after model/fetch budgets
are exhausted. Paid acquisition is skipped when no useful inference budget remains.
A resource-blocked root records an internal `stop_reason=resource_limit`; the legacy
public stale wording is still the separate PR 32 state-contract change.

No resource ceiling increases, review-dashboard dependency, background forever-loop,
new provider, migration or paid capture is introduced. Closing or polling a report
does not buy more research. User-visible automatic waiting/UI delivery remains PR 32/33.

## Acceptance and rollout

Tests exercise the previously missing populated-scope path: first create a public
candidate, then supply another source and an ordinary `NEW` decision at 0.2 match
confidence. Both candidates become publicly readable with the disclaimer, and no
operator review identity is fabricated. Tests also cover sensitive/named/held claims,
ambiguous/targeted graph decisions, terminal budget exhaustion, and an unresolved
first claim followed by a supported later passage in the same long document.

The existing 31G provider recording is replayed without new model/search calls to
check extraction, geography, public-pattern and withholding regressions. That replay
is not live proof of the new policy. The populated-data and long-document tests are
controlled integration cases, not a claim that every real article now yields findings.

Review/merge and deploy a matching API/worker image before assessing ChatGPT behavior.
Then perform a bounded populated-location refresh, confirm new root-linked public
candidates/evidence rather than only retained older cards, verify disclaimers/scope
in ChatGPT, and verify terminal partials stop instead of promising progress forever.
Do not automatically approve existing pending/held records for the live test.
Production is unchanged by this PR preparation.

Validation: **955 passed, 10 skipped**, Ruff passed and the production image built.
The no-network 31G replay remains 9/9 grounded positives, 7/7 geography, 5/6 eligible
public patterns, 6/6 negative safety and 9/9 withholding safety in both cold/warm runs.
No new provider calls or production reports were used for these tests.
