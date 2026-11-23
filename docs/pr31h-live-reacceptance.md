# PR 31H live reacceptance — 2026-11-08

Merged release `ab051face5fe7a2f17761d0cc91ccef7c8529fc6` was deployed with
matching API/worker images. API, worker, PostgreSQL, Redis and readiness checks
passed. The existing filtered MCP hostname remains unchanged.

**The live growth gate did not pass. PR 32 remains blocked by research yield,
not deployment health.** No PR 32 implementation was deployed.

## Bounded production observations

| Destination | Root | Queries | Fetches | Documents | Model calls | New-policy public candidates |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Bangkok | `97eba77c-18bf-4133-95a6-95738f47ea6b` | 6 | 10 | 4 | 24 | 0 |
| Pattaya | `f4d4604e-45db-4d10-a6f6-5c65a5abbb26` | 3 | 20 | 9 | 36 | 0 |

These are consumed/reserved cursor counters, not invoices or guarantees that every
reservation produced a successful response. Both roots ended partial and stopped.
Pattaya ran from 11:54:25 to 11:55:37 UTC, approximately 72 seconds across initial,
enrichment and continuation jobs. Waiting longer cannot advance a terminated root.
Legacy reads call these terminal incomplete roots `stale`; correcting that remains
the planned PR 32 contract work, not evidence that their source data expired.

Bangkok retained its one older Grand Palace candidate. It was not counted as new
publication. Its current extraction/resolution outcomes included unresolved geography,
a sensitive ambiguous graph decision and grounding failures.

Pattaya returned no public pattern. Three retained v6 claims had unresolved locations
and held publication status. Other passages produced empty extractions or failed
grounding; blocked/empty sources and irrelevant passages also consumed work. The
root exhausted its 36-call model allowance. These observations do not establish that
Pattaya has no reported scams, nor that any withheld claim should be published.

Read-only checks joined public cards to lifecycle events, `hypothesis-v4`
resolutions and root documents, requiring event creation after root start. This
avoids counting older findings merely because a document was reused. No eligible
new publication was observed, so no new-candidate ChatGPT evidence test was claimed.
No reports were submitted, held records approved, limits raised or extra roots
started after these two bounded checks.

## Next investigation

The controlled replay and populated-scope tests still support the policy change,
but they do not demonstrate adequate yield from real long articles. Before declaring
the prerequisite passed, diagnose the failed field-level grounding and source-local
geography on the retained passages. Test any repair against fixed real-passage cases
and negative controls before buying another live run. Preserve quotes, named/sensitive
holds, actual scope and cumulative budgets; do not substitute the search destination
for missing source geography or relax publication checks to manufacture a passing run.

Local ignored diagnostic artifacts are under `.tools/pr32/`; they contain sanitized
publication/metrics snapshots. They are not committed fixtures or independent quality
benchmarks. The observed production result, including this failed gate, is recorded
here so the next PR does not mistake a healthy deployment for research acceptance.
