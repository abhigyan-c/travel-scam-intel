# Public intelligence services (PR 27)

`IntelligenceService` in `app/services/intelligence.py` is the shared application
boundary for REST (PR 28) and MCP (PR 31). Frozen, extra-forbid Pydantic response
models live in `app/schemas/intelligence.py`. Collections carry
`contract_version: intelligence-v1`. They contain structured records, never an LLM
answer or invented findings. No migration or provider credential is required.

## Service contracts and review gate

Construct `IntelligenceService(async_sessionmaker, embedder=local_embedder)`.
All methods are async; UUIDs identify persisted locations and hypotheses.
`as_of` optionally supplies one UTC reference time for reproducible reads.

| Method | Required inputs | Result |
| --- | --- | --- |
| `search_location_scams` | `location_id` | `HypothesisPage`: requested location, research, cards, page metadata, outcome |
| `search_scams` | `location_id`, `q` | Same page, literal case-insensitive public summary search |
| `get_hypothesis` | `hypothesis_id` | `HypothesisCard`; optional `requested_location_id` controls scope label |
| `get_hypothesis_evidence` | `hypothesis_id` | `EvidencePage`: public provenance, excerpts, credit, page metadata |
| `match_incident` | `location_id`, `incident` | `IncidentMatches`: status, completeness, similarity and cards |
| `get_recent_patterns` | `location_id` | `HypothesisPage`, with `days` in 30/90/365 (default 30) |
| `get_related_hypotheses` | `hypothesis_id` | `RelatedPage`: relation type, direction and visible target card |
| `get_location_summary` | `location_id` | `LocationSummary`: separate exact, inherited, descendant counts and categories |

Search pages accept `category`, `limit` (1–20, default 20), `offset` (0–1000),
`inherit_parents` and `include_descendants` (both default true). Evidence and related
pages accept the same pagination bounds. Search text is 1–200 characters, category
at most 100. `%` and `_` are literal search characters. Ordering is stable by UUID,
with exact-location cards first. Counts include only public current data;
`has_more` refers to that filtered result. Category summaries contain at most 20
categories and explicitly indicate `categories_complete`.

`IntelligenceInputError` denotes invalid service arguments. `IntelligenceNotFound`
uses `location_not_found` or `hypothesis_not_found`; missing, held-only, merged and
inactive hypotheses have the same public not-found behavior. Transport adapters
must validate incoming UUIDs/types and map these errors consistently.

A card includes a supporting claim summary as title/description, category, lifecycle
status, actual location and full location path, geographic scope, actual-location
research state, and separate metrics. Internal canonical labels can include held
associations, so neither their text nor internal category is used in public search,
matching or cards. Summary/category come from the first eligible supporting claim
in stable claim UUID order. They describe reported mechanisms, not verified events.

This is the PR 27 contracts review gate. Inspect the response models and this table
before PR 28, especially inheritance, visibility, unknown metrics and empty outcomes.

## Geography and research state

A Nadi request can return a Fiji hypothesis labeled `ancestor`, a Nadi hypothesis
labeled `exact`, and a Nadi neighborhood hypothesis labeled `descendant`. Each
retains its original location; Suva hypotheses are excluded. Inheriting a country
pattern does not establish city-specific evidence or mark the city researched.
Related hypotheses may have scope `other` and retain their own location. Directed
`SUBTYPE_OF` relations distinguish incoming/outgoing; other relations are symmetric.

Research metadata exposes only status, initial/enriched completion flags,
in-progress/stale/complete flags, last success and fresh-until timestamps. An expired
ready state is projected as stale without updating the database. Job identifiers,
leases, internal progress, provider errors and scheduler details are never
serialized. Page research refers to the requested location; card research refers
to the actual observation location.

`outcome` is `findings` when any matching record exists, `nothing_found` only when
research is ready and complete, otherwise `research_incomplete`. Empty partial,
failed, unresearched or stale queries never imply that a place has no scams.

Each call owns a PostgreSQL **read-only, repeatable-read transaction**. It does not
perform research, enqueue jobs, update snapshots, fetch raw objects, call Sarvam or
Serper, or attach an incident as evidence. The PR 24 research orchestrator remains
separate. Future adapters can invoke it when appropriate, then read this public
projection; they must not serialize the older internal result's hypothesis scalars
or progress dictionary.

## Public evidence and metrics

Eligible evidence must be current, at the hypothesis's exact location, have explicit
boolean `sensitive=false`, no named entities, and an allowed publication status.
Its processed English document must match its clean hash; the extraction hash and
a nonempty evidence quote must match the normalized document. Traveler reports
must be processed/accepted and have no unaccepted attachment. Hypotheses require
at least one eligible supporting claim and must be current and unmerged.
Contradicting, related and uncertain evidence remain visible when eligible;
only supporting/contradicting evidence contributes to corroboration.

Public scores are a read-only projection of **eligible public evidence only**, using
the existing pure duplicate, independence, quality, confidence and frequency
policies. They do not reuse potentially stale cached scores or historical components
that mention held records. Copy groups are rebuilt within the public cohort;
operator-created groups are preserved with a limitation. Hidden records cannot
inflate or depress public metrics, leak their count, or expose comparison IDs.
The projection policy is `public-intelligence-v1`; audit histories remain unchanged.
`discounted` means independence credit fell below its provenance baseline.

Metrics keep corroboration/confidence, recency and prevalence separate. They include
raw and independence-weighted report counts, occurrence and reporting windows,
date coverage, dataset trend, reference time, completeness and safe limitations.
Corroboration is a heuristic, not truth probability; observed reporting is not
population incidence. Recent patterns require a consistent, nonfuture occurrence
date within the window, never a substitute publication/ingestion date.

At most 200 eligible evidence records are scored per hypothesis. Overflow or invalid
metadata yields `metrics.status=unavailable`, null scores, label `unknown`, and a
limitation. It never publishes a score based on a truncated cohort. Evidence
pagination and total remain correct beyond the scoring cap; unavailable per-record
weights are null. Computed metrics can also be incomplete when semantic agreement,
dates or correlation require review.

Web provenance includes source type, observation class, a safe public link, bounded
claim (400 characters) and quote (500), publication/occurrence dates and public
weights. Links with query parameters, credentials, private hosts or PII are omitted.
Contact details are redacted; threat text is withheld. Anonymous reports expose
their declared FIRST_HAND/WITNESSED/SECOND_HAND label, date and credit, but no
narrative, excerpt, link, author, report/claim/document/source identifiers, object
paths or attachments. The evidence UUID supports public navigation.

## Incident matching limits

Matching requires an explicit persisted location, 20–2000 incident characters, and a
similarity threshold in 0–1 (default 0.7). Contact details are removed before local
embedding. Input is never echoed, stored or sent to an external model. Matching
uses only public supporting summaries in the same geographic scope as search,
not internal hypothesis embeddings derived from potentially held material.

At most 50 candidates are ranked by local PostgreSQL text relevance, then compared
with the pinned local BGE model. The embedder loads cached files only
(`download=False`), uses batches of at most 16, validates vectors, and has a 10-second
embedding deadline. Only returned matches have their evidence metrics projected.
Candidate overflow sets `complete=false` and `candidate_limit`; this is not an
exhaustive semantic scan. The similarity threshold remains provisional.

Status is `matched`, `no_match`, or `unavailable`. Missing/corrupt embeddings or
model failure return unavailable, never a fabricated no-match. An empty public scope
can return no-match without loading a model. Matching does not verify the incident
or establish independent corroboration.
