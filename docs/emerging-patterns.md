# Emerging-pattern review (PR 29)

`app/emergence.py` defines the deterministic `emergence-v1` policy.
`app/db/emergence.py` evaluates one hypothesis and maintains a private review queue.
Normal research/report claim application runs detection after evidence scoring.
There are no new LLM calls, downloads or provider credentials. Existing ordinary
public patterns remain governed by PR 27 eligibility; detection and review never
add a public emerging label, release held allegations, or change confidence.

Apply Alembic migration **0020** before running the updated API/worker. It adds
immutable assessment records, one current review per hypothesis, and append-only
operator decisions. The migration has a tested downgrade/upgrade path.

## Separate signals

| Signal | Measurement | Interpretation |
| --- | --- | --- |
| Dataset novelty | Earliest eligible supporting claim creation or normalized report submission | First observed in the eligible dataset, not a new scam in the world |
| Growth | Current 30-day vs previous 30-day independent report credit, grouped by first dataset observation | Growth in the dataset's observed reporting, not population incidence |
| Confidence | Current PR 27 eligible-evidence corroboration score and label | Separate from novelty and growth; neither increases this score |
| Geography | Older similar public support at another established, non-ancestor/descendant location | Possible new dataset appearance; not proof of geographic spread |
| Similarity | Cosine of compatible, hash-current cached supporting-claim vectors | Review hint, not evidence that two mechanisms are identical |

Dates of publication and occurrence retain their existing separate meanings.
An old published account first extracted today can be new to this dataset without
being a newly occurring event. Historical supporting links, including archived
links still satisfying other public eligibility rules, preserve the first observed
date across source-version changes. Archived links contribute no current growth
credit. Historical dates are evaluated under **current eligibility**, not as a
reconstruction of what the database contained at a past date.

Each external source or normalized report counts once; multiple claims/versions
receive the maximum current independence credit within that report. Copies,
cross-posts, shared authors and similar origins retain the existing independence
discounts. A hundred copied claims do not become a hundred independent reports.
Dates in the future are excluded and flagged. Provisional geography is explicit.

The recent window is age 0–<30 days; the baseline is age 30–<60 days. Growth is
`rising` only when current+previous credit is at least 2, current credit is at least
twice previous credit, and the absolute increase is at least 1. Falling is symmetric;
otherwise sufficient data is stable. A zero baseline yields a null ratio, not
infinity or automatic proof of novelty. Sparse credit yields insufficient data.

A candidate is queued when first observed within 30 days with at least 0.5 recent
credit, or when independent reporting growth is rising. Geographic hints can
accompany a newly observed candidate. Low confidence remains visible as low
confidence; this is a review heuristic, not a calibrated probability. Wording is
limited to “first observed in our dataset” and “emerging candidate.”

## Coverage and reproducibility

Detection reuses PR 27's grounded public evidence projection and pure scoring
policies. Held/named-actor claims, rejected reports, unaccepted media, inactive and
merged hypotheses do not create candidates or contribute current credit. Raw report
narratives, attachments and source authors are absent from assessment responses.
Assessment fingerprints capture relevant evidence/provenance changes without
storing raw text. No public response gains these private signals.

The evidence scoring bound is 200 current eligible records. Overflow or malformed
metadata gives `candidate=null` with limitations, rather than a partial score.
An existing queued candidate then moves to `needs_assessment`. No public support
produces a negative assessment and withdraws an existing queue item on rescan.

Similarity uses the first public supporting claim's valid cached BGE vector. Up to
100 other visible hypotheses, ordered by UUID, are compared; the strongest five
matches at cosine >=0.85 are retained. Missing/incompatible vectors, future dates,
or truncated coverage set `complete=false`. Results are bounded review hints,
not an exhaustive nearest-neighbor search or a global novelty verdict. Internal
hypothesis embeddings, which can contain held associations, are not used.

Assessments retain policy version, reference time, input/material fingerprints and
separate signals. Equivalent inputs/signals within a UTC day reuse one assessment;
explicit `as_of` evaluation provides exact reference-time deduplication for tests
and controlled replay. Current eligibility/credit is used in both reporting
windows. Stored assessments are immutable captured projections, not historical
database replay. An older reference cannot rewind a newer queued assessment.

Hypothesis locks serialize concurrent scans and review revisions. Material evidence
or signal changes reopen a candidate as pending and advance its revision; loss of
the signal withdraws it. Clock-only equivalent reassessments advance the reference
without reopening an acknowledged/dismissed decision. Each operator decision records
the assessed snapshot, resulting revision, action, reason and timestamp. The existing
single configured admin token identifies the operator role.

## Private admin API

All routes require the existing bearer `ADMIN_API_TOKEN` (at least 32 characters).
Responses use `Cache-Control: no-store`. Public REST routes do not expose the queue.

| Route | Use |
| --- | --- |
| `POST /v1/admin/hypotheses/{id}/emergence-assessments` | Rescan one hypothesis using current evidence; returns assessment and review state |
| `GET /v1/admin/emerging-patterns` | Page the queue; status defaults to pending, limit 1–20, offset 0–1000 |
| `POST /v1/admin/emerging-patterns/{id}/decision` | Revision-checked acknowledgement or dismissal |

Other queue filters are `acknowledged`, `dismissed`, `withdrawn`, `needs_assessment`.
Items include `currently_public` so an older assessment can be distinguished from
current eligibility before a rescan. A decision body has `revision`, `action`
(`acknowledged` or `dismissed`), and a nonblank `reason` (at most 500 characters).
Stale/non-pending revisions or lost public support return 409. Acknowledgement is
an audit decision about the captured dataset candidate; it is not publication or
verification. Rescan before reviewing changed evidence. Moderation/lifecycle changes
can make a stored assessment stale until the next rescan.

The PR 30 scheduler can call the same bounded detector for selected research work.
This PR does not add a world scan or periodic scheduler. The PR 35 dashboard can
consume this queue and its revision-safe decisions; its UI is not implemented here.
