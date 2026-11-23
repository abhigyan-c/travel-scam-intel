# Public REST API (PR 28)

The anonymous public API is rooted at `/v1`. Interactive documentation is available
at `/docs`, with the schema at `/openapi.json`. It uses the shared PR 27 intelligence
services and public eligibility rules. No API credential is needed for public reads
or report submissions. Admin and private-media routes remain separate.

## Endpoints

| Method and path | Response | Behavior |
| --- | --- | --- |
| `GET /v1/locations/{id}/hypotheses` | `HypothesisPage` | Public patterns; literal summary search, category, geographic and page filters |
| `GET /v1/hypotheses/{id}` | `HypothesisCard` | One visible current pattern; optional requested-location scope context |
| `GET /v1/hypotheses/{id}/evidence` | `EvidencePage` | Paged public excerpts, dates, provenance and credit |
| `GET /v1/hypotheses/{id}/related` | `RelatedPage` | Paged visible related patterns with relation direction and actual geography |
| `POST /v1/hypotheses/match` | `IncidentMatches` | Read-only local comparison of an incident against public patterns |
| `GET /v1/locations/{id}/recent` | `HypothesisPage` | Patterns with a consistent occurrence date in the selected window |
| `GET /v1/locations/{id}/summary` | `LocationSummary` | Exact/inherited/descendant counts, category counts and research state |
| `POST /v1/reports` | `ReportReceipt` (202) | Existing idempotent anonymous intake and asynchronous processing |

Use existing `GET /v1/locations?q=...` and `/v1/locations/resolve?q=...` to find a
persisted location UUID. Ambiguous names require location context; no endpoint
silently guesses a country. Unknown UUIDs return 404.

## Filters and bounded pages

Location hypothesis and recent reads support:

- `q`: literal case-insensitive substring of an eligible supporting summary,
  1–200 characters; `%` and `_` have no wildcard meaning.
- `category`: exact public claim category, at most 100 characters.
- `inherit_parents=true`: include broader patterns labeled with their actual location.
- `include_descendants=true`: include the requested location's descendants, never siblings.
- `limit=20` (1–20) and `offset=0` (0–1000).
- `research=true`: request shared background research if needed; use false for a
  read-only query. Summary accepts this flag too.

Recent reads also accept `days=30`, `90` or `365`. Publication dates are never used
as occurrence dates. Evidence and related reads accept only limit and offset.
Detail reads accept `requested_location_id` to label the actual geographic relation.
Unknown query/body fields, blank filters and out-of-range values return 422 before
research reservation. Inputs are trimmed; IDs are validated UUIDs. There is no
client-supplied score reference time.

Pages return `items`, filtered `total`, `limit`, `offset` and `has_more`, in stable
UUID order with exact-location cards first. This is offset pagination across
requests, not a frozen multi-request snapshot. Concurrent public changes may alter
later pages. Collection contracts are versioned `intelligence-v1`.

## Research and partial results

By default, location hypotheses/recent/summary reads reserve and dispatch research
for unresearched or stale locations using the existing PostgreSQL lease and durable
outbox. Concurrent readers share the same job; fresh reads do not enqueue work.
Initial research also runs in the worker, so an HTTP request does not wait for
Serper, page fetching, Sarvam extraction or enrichment. Failed research respects
the existing retry backoff. No credentials or paid calls are needed to read cached
intelligence; background discovery requires the existing `.env` configuration.

The response is always the PR 27 public projection, never the internal research
reader. Existing findings remain available during refresh or queue outages.
Only compact research flags and freshness timestamps are exposed; provider errors,
job IDs, leases, progress dictionaries and outbox details are private. Redis failure
does not discard the committed research request; worker dispatch repairs it later.

All successful query/match responses are HTTP 200 with `Cache-Control: no-store`.
Clients must inspect `research`, `outcome`, metric availability and completeness:

- `findings`: at least one matching public pattern is available.
- `nothing_found`: no matching pattern and current research is ready and complete.
- `research_incomplete`: empty results with unresearched, partial, in-progress,
  failed or stale research. This does not mean a destination is scam-free.

Poll `GET /v1/locations/{id}/summary?research=false`, then reread the desired page.
Detail/evidence/related reads and incident matching never trigger research.

## Incident matching

`POST /v1/hypotheses/match` accepts this JSON shape:

```json
{
  "location_id": "00000000-0000-0000-0000-000000000001",
  "incident": "A driver demanded additional money after an agreed fixed fare.",
  "limit": 5,
  "min_similarity": 0.7
}
```

Replace the illustrative UUID with a resolved location. Incident text must contain
20–2000 characters, limit is 1–20 and similarity is a finite number in 0–1. Input
contact details are redacted before local embedding; the incident is not echoed,
logged as content, stored or attached as evidence. The cached local BGE model is
loaded lazily, without downloading at request time.

Statuses `matched`, `no_match`, and `unavailable` are distinct. Unavailable means
comparison could not run (for example a missing model), not that no pattern exists.
Check `complete` and limitations for the 50-candidate bound. Similarity is neither
incident verification nor a confidence probability. An empty public scope can return
no-match without loading the model. See the full
[service contracts and limits](intelligence-services.md).

## Provenance and errors

Cards retain their real location, scope, confidence, recency and observed reporting
metrics. Held, named-actor, inactive and merged records are excluded. Evidence
responses redact contact details, bound web excerpts and label anonymous report
provenance. They never expose private report narratives, attachments, author/report/
claim/document/source IDs or object paths. Missing and nonpublic hypotheses both
return the same 404 message. Scoring overflow returns unknown metrics, with correct
evidence pagination, rather than a truncated score.

Errors use the existing envelope and `X-Request-ID` header:

```json
{"error":{"code":"not_found","message":"Hypothesis not found","request_id":"example-request"}}
```

404 denotes missing/nonpublic records; 422 denotes invalid input; database failures
return 503 (`http_error`) with a safe message. Unexpected errors return 500
(`internal_error`). Request validation uses `validation_error` with field locations
and error types, never submitted values or validator context. OpenAPI documents
the actual envelope rather than FastAPI's default validation body. Report intake
also documents its 409 idempotency conflict and 503 storage response; its existing
202/replay/media workflow is unchanged.
