# PR 22 scoring review

Policy: `confidence-v1`. This is an internal corroboration heuristic, not a calibrated
probability of truth. The numerical constants and labels are provisional review choices.
PR 22 is a manual review gate; review this behavior before starting PR 23.

For each active attachment, start with PR 21's freshly calculated eligible weight
(`source quality × independence`). Multiply by semantic agreement and location agreement:

- Semantic agreement: the stored attachment match score, or 0.5 when missing.
  This is match-decision confidence from resolution/review, not new LLM truth assessment.
  It is used only as a conservative heuristic; extraction confidence is not used.
- Location agreement: 1 for the exact hypothesis location, 0.5 for unresolved geography,
  0 for another location. Parent/child or nearby places do not automatically agree.
- Supports contribute to S; contradicts contribute to C. Related/uncertain contribute zero.
  Held, sensitive, named-actor, rejected, pending and otherwise ineligible records
  already have zero PR 21 weight and cannot add support, contradiction or diversity.

The result is `D × S / (2 + S + 2 × C)`. Thus contradiction reduces corroboration;
contradiction alone cannot create corroboration. D starts at 0.8 and adds up to 0.1
for supporting source-type diversity and up to 0.1 for supporting platform diversity.
Each bonus reaches its cap at three distinct categories. Only positive effective
support counts toward diversity. Unknown platforms add no diversity. Authors, threads,
copying and cross-posting are discounted by the preceding independence calculation.
Platform diversity is a metadata heuristic, not proof of editorial independence.

Labels: `no_support` for S=0; otherwise `limited` below 0.25, `moderate` below 0.6,
and `strong` from 0.6. Labels do not promote, publish, hide or change a hypothesis status.
An ordinary low-score candidate remains queryable. Upstream incomplete provenance
assessments retain `complete=false`, their review reasons and an explicit explanation;
unknown semantic/location metadata adds its own review reason. Cohorts beyond the
configured bound fail rather than quietly score a truncated set.

## Hand-built scenarios inspected

Unless specified, each report below has unit eligible independent weight, exact
location, full semantic agreement, one source type and one platform. These scenarios
isolate the formula; real anonymous reports generally have lower PR 21 weights.

| Scenario | Score | Label |
| --- | ---: | --- |
| No evidence | 0 | no_support |
| One supporting report | 0.266667 | moderate |
| Five independent supporting reports | 0.571429 | moderate |
| 101 independent supporting reports | 0.784466 | strong |
| 105 independent supporting reports | 0.785047 | strong |
| One supporting report plus 104 copies with zero independent credit | 0.266667 | moderate |
| Five supporting reports plus one equal contradiction | 0.444444 | moderate |
| Five supporting reports plus five equal contradictions | 0.235294 | limited |
| Contradiction without support | 0 | no_support |
| Five supporting reports across at least three types/platforms | 0.714286 | strong |
| One supporting report with unknown semantic and location agreement | 0.088889 | limited |
| Only other-location, related, uncertain or ineligible evidence | 0 | no_support |

The gain from 1→5 is 0.304762; from 101→105 it is 0.000581. Copy count alone
does not raise corroboration. A typical first-hand anonymous forum report with no
author/publication metadata starts at 0.325 PR 21 weight. At full semantic and exact
location agreement its confidence is 0.111828 (`limited`); missing semantic agreement
reduces that further. A high quality fully independent single report can be `moderate`,
but cannot be `strong` under this policy.

## Persistence and operational behavior

`calculate_confidence` refreshes duplicate, independence and weight calculations inside
one transaction, under the existing hypothesis and provenance locks. It validates the
current cohort and weight-run pointers instead of accepting old scalar caches. Callers
commit promptly; normal PostgreSQL deadlock/serialization retries belong to callers.
It hashes the policy, upstream weight run, hypothesis context, stance, location and
semantic inputs, and reuses identical immutable `HypothesisScoreSnapshot` records.
Components preserve per-attachment contributions and links to upstream histories;
raw quotations are not copied into confidence snapshots.

The hypothesis scalar is a current cache; historical snapshots are never rewritten.
Recalculation refreshes it after metadata edits. Existing lifecycle/upstream scoring
operations invalidate affected scalar caches. Direct database edits have no trigger:
consumers must recalculate before relying on current confidence. No API is added here.
Recency and prevalence are left unknown in confidence-only snapshots; PR 23 owns their
measurement. No observation dates or counts are turned into population incidence.

Migration 0016 preserves existing snapshots. Its downgrade refuses to fabricate values
if confidence-only snapshots still contain unknown recency/prevalence. Operators must
resolve that explicitly before reverting to the older non-null schema.

Automated checks cover the scenario table, input validation, deterministic ordering,
history reuse, changed semantic/location inputs, eligibility/copies/contradictions,
concurrent snapshot creation and CLI preview rollback of all scoring stages.
