# Recency and observed reporting frequency

PR 23 policy: `frequency-v1`. These metrics describe reports in the collected dataset.
They do not estimate population incidence, traveler exposure, the probability of being
scammed, or the truth of an allegation. Confidence remains the separate PR 22 measure.

## Report identity and eligibility

Only active, publicly eligible supporting attachments at the hypothesis's exact location
contribute. PR 21's moderation, publication and sensitive/named-actor gates apply.
Contradicting, related and uncertain attachments do not become reports of occurrence;
contradictions still affect confidence. Unresolved or other-location attachments are
excluded from these location-specific counts and listed with exclusion reasons.

The report unit is a normalized `UserReport.id`, or otherwise an external `Source.id`.
Multiple claims and document versions from the same report/source count once. Thus one
article describing several incidents still counts as one report, not several incidents.
Distinct sources copying a report appear in raw counts; their inherited independence
credit is discounted by PR 20. The per-report credit is the **maximum**, not sum, of its
eligible attachments' fresh independence credits. This prevents claim count and document
versions from multiplying corroboration. Report frequency does not multiply by source
quality or semantic match confidence; those belong to confidence, not report counts.

`all_time` includes every eligible report in the current cohort, even if its occurrence
date is missing, conflicting or future. This is a count of reports, not dated events.
Each window exposes both `raw_report_count` and `independence_weighted_report_count`.

## Date semantics

The main occurrence timeline uses a linked normalized report's `occurred_at` when set;
otherwise it uses `Claim.occurred_at`. Publication, retrieval, ingestion, submission and
hypothesis first/last-seen timestamps never substitute for an occurrence date.

One consistent known occurrence date can date a report with multiple attachments. If
known dates disagree, the report is undated for occurrence metrics instead of choosing
the newest. Future occurrence dates are also excluded from dated metrics and flagged.
Snapshots preserve date candidates, date basis and missing/conflict/future reasons.

A separate `reporting_windows` series uses normalized report submission time, or external
document publication time followed by source publication time. When several versions
have publication dates, the earliest is used. Retrieval and ingestion timestamps are
never a fallback for external reporting dates. A recent article about an old event can
therefore increase recent reporting counts without making the event appear recent.
Missing/future reporting dates are exposed on each report and excluded from that series.

All calculations use an explicit UTC reference instant. Naive datetimes are rejected.
The 30/90/365-day windows include ages `0 <= age < N days`: exactly 30 days old is in
the 90-day window and previous 30-day trend period, not the current 30-day period.
Future dates do not enter a window. An `--as-of` reference changes the measurement date
for today's known dataset; it does **not** reconstruct what the database knew historically.
In particular, all-time counts still include currently known undated reports.

## Scores and trends

For valid occurrence-dated reports with independence credit `I` and age in days `a`:

- Decay: `I × 2^(-a / 90)`; the policy's half-life is 90 days.
- Recency: sum of decayed credit divided by sum of dated independent credit.
- Prevalence: `W90 / (2 + W90)`, where W90 is the independence-weighted occurrence
  report count in the last 90 days. This bounded internal score means **observed reporting
  frequency**. It has no population denominator and is not a rate of scams among travelers.

Scores are unknown (`null`) if no occurrence-dated report has positive independence
credit, including an empty dataset. If historical dated reports exist but none are in
the last 90 days, prevalence is zero; this is not evidence of safety. Undated evidence
remains in all-time counts and reduces date completeness. A partial score based on
dated reports includes date coverage and review reasons. Copies with zero credit cannot
refresh recency or create a weighted trend.

Trend compares the last 30 days to the preceding nonoverlapping 30 days, using independent
report credit. With no dated independent credit it is `unknown`; with no credit in either
period it is `no_recent_reports`. Less than 2 combined credits is `insufficient_data`.
Otherwise a change of at least 1 credit and a ratio of at least 1.5 produces `rising`
or `falling`; the remainder is `stable`. These are dataset trends and can change with
collection coverage; they do not claim objective emergence or changing traveler risk.

Examples (unit independent report credit, exact location):

| Collected evidence | Recency | Prevalence | Notes |
| --- | ---: | ---: | --- |
| One occurrence today | 1 | 0.333333 | One report, not an incidence estimate |
| One occurrence exactly 90 days ago | 0.5 | 0 | Half-life boundary, outside the 90-day window |
| One today and one 90 days ago | 0.75 | 0.333333 | Confidence need not change as these age |
| One undated occurrence, publication today | unknown | unknown | Reporting window count is 1 |
| One occurrence 365 days ago, publication today | 0.060139 | 0 | Recent reporting about an old event |
| One old occurrence plus recent copies with zero credit | age-dependent | unchanged | Raw reporting counts may increase |

## Persistence and use

`app.db.frequency.calculate_recency_and_frequency` refreshes duplicate, independence,
weight and confidence stages, then persists a `HypothesisScoreSnapshot` using the existing
schema. `frequency-v1` snapshots contain the reference date, policy, per-report dates and
credits, all windows, coverage, exclusions, trend and upstream confidence/weight links.
Unchanged inputs with the same reference instant reuse the same snapshot. Different
reference instants or date edits produce new history; old snapshots are not overwritten.
There is no new migration or provider request.

The helper does not commit. The cohort limit defaults to 100 (maximum 200); larger
cohorts fail rather than silently return partial counts. Existing hypothesis/provenance
locks serialize same-hypothesis calculations; callers commit promptly and handle normal
database deadlock/serialization retries. Missing dates and incomplete upstream assessment
remain explicit through completeness and review reasons.

Without `as_of`, the helper uses current UTC time to the second and updates the hypothesis's
recency/prevalence caches. An explicit `as_of` saves a reproducible snapshot without
overwriting those current temporal caches. It still refreshes the current provenance and
confidence stages, which can invalidate caches if evidence inputs changed. Cached temporal
values age: consumers must recalculate for a current answer instead of treating scalars
as permanently fresh. No public endpoint or scheduler is introduced in this PR.

CLI:

```powershell
uv run python -m app.calculate_frequency <hypothesis-uuid> --dry-run
uv run python -m app.calculate_frequency <hypothesis-uuid>
uv run python -m app.calculate_frequency <hypothesis-uuid> --as-of 2026-10-28T12:00:00Z --dry-run
```

`--dry-run` rolls back all scoring histories, duplicate/group updates and scalar caches.
Only internally authorized tooling should expose full snapshot components: they contain
provenance IDs for excluded attachments, not a public evidence-response contract.
