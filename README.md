# Travel Scam Intelligence

An evidence-backed travel scam intelligence backend. The repository currently provides the
API, local infrastructure and core domain models; research and ChatGPT integration arrive in later PRs.

## Local development

Install Docker Desktop (or Docker Engine with Compose on Linux), then start the full stack:

```powershell
docker compose up --build --wait
```

This starts the API, PostgreSQL 17 with pgvector, Redis, and MinIO. The API applies Alembic
migrations and creates the configured storage bucket before serving requests. The initial build
compiles MinIO from a pinned [official source release](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z)
because official registry images are unavailable; later builds reuse the Docker cache.

All published ports bind to localhost. API: `http://localhost:8000`; PostgreSQL: `5432`;
Redis: `6379`; MinIO S3: `http://localhost:9000`; console: `http://localhost:9001`.
`docker compose down` stops the stack and retains its named volumes.

Compose works with local development defaults. To customize settings, copy `.env.example` to
the ignored `.env` (`Copy-Item .env.example .env` on PowerShell, `cp .env.example .env` elsewhere).
The example's credentials are local development values. Keep real credentials in `.env`.

Settings: `APP_NAME` (default `Travel Scam Intelligence`), `APP_ENVIRONMENT`
(`local`, `test`, or `production`), and `APP_LOG_LEVEL` (default `INFO`). Environment variables
override `.env`. Unknown `.env` keys are ignored so later service credentials can coexist.
Infrastructure settings use `DATABASE_URL`, `REDIS_URL`, `S3_ENDPOINT`, `S3_BUCKET`,
`S3_ACCESS_KEY`, and `S3_SECRET_KEY`. `APP_READINESS_TIMEOUT_SECONDS` defaults to 3 per check.
Compose builds its container database URL from `POSTGRES_USER`, `POSTGRES_PASSWORD`, and
`POSTGRES_DB`, and uses service hostnames for Redis and MinIO. The URLs in `.env.example`
are for an API running on the host. When changing database credentials, keep the host URL in
sync; URL-encode reserved characters in the URL. For passwords containing URL-reserved characters,
also set `COMPOSE_DATABASE_URL` to the encoded URL using host `postgres` and port `5432`.
Host defaults use `127.0.0.1` to match the published IPv4 ports without IPv6 connection delays.
PostgreSQL credentials initialize a new
volume; changing `.env` does not change existing database users.

For host development, install [uv](https://docs.astral.sh/uv/getting-started/installation/),
copy `.env.example` to `.env`, and run:

```powershell
docker compose up -d --build --wait postgres redis minio
uv sync --locked
uv run --locked alembic upgrade head
uv run --locked python -m app.db.seed
uv run --locked python -m app.integrations.storage
uv run --locked uvicorn app.main:app --reload --no-access-log --loop asyncio:SelectorEventLoop
```

The explicit selector loop supports Psycopg async connections on Windows. uv uses Python 3.12
from `.python-version` and the committed lockfile. No Search or LLM API keys are needed yet.

- `GET /`: application name and version.
- `GET /health`: process liveness (`{"status":"ok"}`), independent of backend availability.
- `GET /ready`: HTTP 200 when PostgreSQL and its vector extension, Redis, and the storage bucket
  are available; otherwise HTTP 503. Returns each dependency's status without connection details.
- `/docs`: interactive API documentation.

## Locations and sources

Startup seeds Thailand, Bangkok, Grand Palace, Wat Pho, Fiji, Nadi and Suva, plus the `BKK`
alias for Bangkok. Repeat seeding with `uv run --locked python -m app.db.seed`; it is idempotent.

- `GET /v1/locations?q=palace`: search canonical names and aliases; supports `country_code`,
  direct `parent_id`, `limit` (1–100) and `offset`.
- `GET /v1/locations/resolve?q=BKK`: exact normalized lookup, returning `resolved`, `ambiguous`
  or `not_found`. Country/parent filters can disambiguate; responses include the total count
  and at most 20 matches. Unknown queries do not create records yet.
- `GET /v1/locations/{id}`: location fields, parent ID and aliases; HTTP 404 for an unknown ID.

Canonical names and alias lookup keys use Unicode NFKC normalization, whitespace collapse and
case folding. Display names retain their spelling. Locations have a parent hierarchy and a
provisional flag; coordinates are optional and are not inferred. Internal location resolution can
create provisional records with explicit type and parent context; public lookup remains read-only.

Source records identify individual articles/threads/reports and retain platform, canonical URL,
author, type, publication/retrieval times and JSON metadata. Canonical URLs are unique when
present; traveler reports can have no public URL. The ORM field `source_metadata` maps to the
database column `metadata`. Source writes remain internal.

## Documents and provenance

Documents represent acquired source versions. Each requires a source ID, a bucket-relative raw
object key (`raw_object_path`), and a lowercase SHA-256 hash of the raw bytes (`content_hash`).
The source foreign key is mandatory; deleting a source referenced by a document is rejected.
URLs are optional for traveler reports and imported material. Document metadata uses the ORM
field `document_metadata`, mapped to the database column `metadata`.

Internal helpers in `app/db/documents.py` create, get, list, and update processing results.
Creation reuses an existing `(source_id, content_hash)` version without overwriting its original
acquisition fields or processing results. Changed bytes create a new version; identical bytes
from different sources retain separate provenance. Reads eagerly load the source.

`set_document_processing` changes only status, clean text, language and metadata. Metadata is
merged at the top level, preserving unspecified keys. Statuses are `pending`, `processed` and
`failed`. Repository helpers use the caller's transaction and do not commit. Raw acquisition
fields are protected by the helper's interface; direct database writes remain an operator's
responsibility. These helpers add no public endpoints. Acquisition and processing pipelines
arrive in later PRs.

Responses include `X-Request-ID`. A supplied ID is accepted only if it is 1–64 ASCII characters,
starts with a letter or digit, and uses letters, digits, `.`, `_`, or `-`; otherwise a new ID is
generated. Errors use `{"error":{"code":"...","message":"...","request_id":"..."}}`,
with field locations/types added for validation failures. Logs are JSON and include request IDs,
method, path, status and duration, without request bodies, headers or raw query strings.

## Claims and hypotheses

Claims retain their document ID and, through that document, their source provenance. A claim's
location is optional while resolution is pending; unresolved place text can remain in
`structured_data`. Claim processing statuses are `pending`, `processed` and `failed`.
Claim type and hypothesis category are nonblank strings; extraction will define their taxonomy.

Each hypothesis requires one primary location and starts as `candidate`. Other persisted statuses
are `active`, `disputed` and `inactive`; lifecycle operations arrive later. Scores are optional
values from 0 to 1, with null meaning unscored. Observation timestamps are either both absent or
an ordered `first_seen`/`last_seen` pair. They are not inferred from record creation time.
Foreign keys prevent deleting referenced documents or locations.

Both models have nullable `vector(384)` embeddings for the planned separate
`BAAI/bge-small-en-v1.5` embedding model. Sarvam 105B remains the LLM for semantic extraction.
These models provide persistence; embedding generation, semantic matching,
scoring and public endpoints arrive in subsequent PRs. No API credentials are needed yet.

## Evidence and hypothesis relationships

`Evidence` attaches one claim to one hypothesis with an explicit `supports`, `contradicts`,
`related` or `uncertain` stance. A claim/hypothesis pair is unique regardless of stance;
different claims can retain conflicting observations for the same hypothesis. Relationships
provide the path from evidence through claim, document and source. Referenced claims and
hypotheses cannot be deleted.

Semantic match, source quality, independence and evidence weight are nullable values from 0 to 1.
Null means not calculated; this schema does not calculate scores or collapse duplicate reports.
`duplicate_group_id` optionally references a `DuplicateGroup`. Multiple evidence rows can share
a group without losing their individual claims or stances. Referenced groups cannot be deleted.

`HypothesisRelation` links `source_hypothesis_id` to `target_hypothesis_id` using `SAME_AS`,
`RELATED_TO`, `SUBTYPE_OF` or `CONTRADICTS`. Self-links and exact duplicates are rejected.
The database also rejects reversed duplicates for symmetric types; `SUBTYPE_OF` preserves
direction. Different relation types can coexist between a pair. Neither subtype cycles nor
transitive equivalence are inferred or validated yet. Links retain their endpoints through
restrictive foreign keys; they do not merge hypotheses or change confidence. These writes
remain internal, with no new public endpoints.

## Reports, score history and schema review

`UserReport` stores anonymous report text, an explicit `FIRST_HAND`, `WITNESSED` or `SECOND_HAND`
provenance class, optional location/occurrence time and a required unique normalized document ID.
The document retains the source link. Report submission and normalization arrive in PR 25;
callers must create a source of type `user_report` and its document before attaching the report.
The database enforces document existence and uniqueness, but does not enforce source type across
tables. No user account or public submission endpoint is introduced here.

`HypothesisScoreSnapshot` retains three scores (0–1), a scoring version, explanation, JSON
components and creation time. New calculations should insert new snapshots; changing current
hypothesis scores does not overwrite history. Direct database updates remain possible; append-only
service behavior and score calculation arrive with the confidence engine. Referenced hypotheses
cannot be deleted. Document and claim statuses now share `ProcessingStatus`; their existing
import names and database constraint names remain compatible.

PR 07 is the foundational schema review gate. Review the complete location → hypothesis →
evidence → claim → document → source path, report lineage, duplicate membership and score history
before starting PR 08. Integrity tests cover these links and deletion safeguards. Migration 0006
adds the duplicate-group foreign key without inventing groups: any pre-existing dangling group
IDs must be resolved before upgrading.

## URL ingestion

After starting the stack, ingest a public article with:

```powershell
uv run --locked python -m app.ingest https://example.org/article
```

The command prints the persisted document UUID. Internal callers can pass a `DiscoveryResult`
to `app.db.ingestion.ingest_url`, sharing a `Fetcher` across requests for per-host pacing.
The helper uses the caller's transaction and does not commit. No public endpoint is added.

URLs allow HTTP/HTTPS on standard ports, normalize hostname/default port/fragment and retain query
order and parameters. Credentials and control characters are rejected. Every hop resolves and
checks all addresses, rejects non-public destinations, and pins the checked address for the TCP
connection while retaining the original hostname for Host and TLS verification. Redirects are
limited to three and cannot downgrade HTTPS. Environment proxy settings are not used.

Defaults are a 25-second overall deadline, five seconds per request, two attempts, 0.5-second
spacing per host per Fetcher, and a 2 MB raw-body limit. Only HTML, XHTML and plain text with
identity encoding are acquired. Transient connection errors and selected HTTP failures retry;
other failures remain explicit. Limits are constructor options. Cancelled blocking socket work
can finish in its worker thread within the request bounds. Share Fetcher instances within a
worker; distributed host throttling arrives with worker coordination.

Ingestion hashes raw bytes and conditionally writes a deterministic object key before creating
source/document records. Source identity uses the requested canonical URL; the document retains
the final URL after redirects. Unchanged bytes reuse the version and preserve processing results;
changed bytes create a new version. Different requested URLs remain separate sources even if
they redirect to the same page. Upload failure creates no source or document. A later database
failure can leave an unreferenced raw object; retry reuses that key without overwriting it.
Object cleanup and redirect alias reconciliation are future maintenance work. Acquisition does
not extract claims or infer publication dates.

## Serper discovery

Set `SERPER_API_KEY` in ignored `.env`, then run:

```powershell
uv run --locked python -m app.discovery "Bangkok" --queries 3
```

This calls the [Serper search API](https://serper.dev/) with English search settings. Six
deterministic query templates cover tourist scams, common scams, tourist traps, taxi scams,
tourist fraud and Reddit reports. Defaults execute the first three with a 25-second deadline
and return up to 30 candidates. The provider has a ten-second socket timeout and no automatic
retries. `--queries 1` makes a single-request smoke test.

`SearchProvider` is a single search-method boundary. Serper results normalize into `SearchResult`,
which can be passed directly to ingestion. Discovery canonicalizes URLs, excludes obvious local
addresses and unsupported file types, deduplicates exact canonical URLs and ranks by search
position, query order and URL. Query parameters remain intact. DNS safety is checked again by
the fetcher. English settings do not guarantee that every returned page is English; document
language filtering arrives in PR 10. Snippets are discovery hints, not evidence or extracted claims.

Responses include per-query request counts, raw result counts, provider-reported credits when
available and sanitized error codes. Counts describe attempts, not guaranteed billing; timed-out
requests can still consume credits. Usage is returned per run; durable job accounting arrives
with the research worker. `empty` means successful searches returned no usable candidates;
`failed` and `partial` distinguish provider failures and incomplete work. The provider never
creates Source records: ingestion records the underlying webpage/platform. No public endpoint
or new external credentials are required for startup or fixture tests.

## Document processing

`app.db.processing.process_document(session, document_id, storage=client, bucket=bucket)` reads
bounded raw content, verifies its SHA-256 hash, extracts article text with Trafilatura, and detects
language with seeded langdetect. It uses the caller's transaction. It performs no crawling or
LLM calls. HTML/XHTML and plain text are supported; scripts, navigation and common boilerplate
are removed by the article extractor. This is heuristic extraction, not a browser renderer.

Clean text and its SHA-256 hash are derived fields. Title, author, date and canonical URL, when
found, are stored under `document_metadata.processing`; acquisition fields and Source identity
are preserved. Reported canonical URLs are metadata hints and are never fetched automatically.
HTML language declarations do not override detected language. At least 40 alphabetic characters
and a language probability of 0.9 are required. Only detected English is eligible for extraction;
non-English and uncertain-language text is retained for review. Those outcomes are `processed`,
with `eligible_for_extraction=false`. Empty, blocked, unsupported, corrupt and unavailable raw
content produce explicit failure outcomes. Captcha/title detection is heuristic and incomplete.

`find_exact_duplicates` returns paginated documents sharing the raw hash or a non-null clean-text
hash, excluding the input document. Cross-source matches retain separate provenance. This does
not merge Sources, attach evidence groups or calculate independence. Migration 0007 adds the
clean hash and lookup indexes; existing documents acquire clean hashes when reprocessed.

## Background jobs

Compose starts an arq worker after the API completes migrations and storage initialization.
The worker runs in Linux containers, including on Windows hosts. Its health check uses arq's
Redis heartbeat. Run `docker compose logs worker` to inspect sanitized job failures.

```powershell
uv run --locked python -m app.jobs ingest https://example.org/article --key article-001
uv run --locked python -m app.jobs process DOCUMENT_UUID --key process-001
uv run --locked python -m app.jobs research Bangkok --key research-001
uv run --locked python -m app.jobs status JOB_UUID
uv run --locked python -m app.jobs dispatch
```

Submissions commit `ResearchJob` in PostgreSQL before Redis publication. Reusing a key with the
same normalized payload returns the same job; a different payload is rejected. Explicit new keys
request new work. The research handler runs the bounded search-to-claims pipeline below and records partial results
and usage. Hypothesis scoring and location-level freshness remain later stages.

PostgreSQL tracks queued/running/retry/succeeded/failed states, attempts, results, error type and
an expiring lease with an ownership token. Four jobs run concurrently, each with a 55-second
application deadline, a 60-second arq timeout, a 90-second lease and at most three actual attempts.
Successful jobs ignore duplicate delivery. Domain changes and successful status commit together;
failed document processing retains its explicit processing outcome. Redis job IDs deduplicate
delivery, while database keys and existing ingestion constraints protect durable records.

Worker startup dispatches up to 100 pending/retry/expired jobs. `dispatch` repeats that bounded
recovery sweep when publication fails or Redis is restored; no periodic scheduler is added.
Transient failures retry with short delays. Worker cancellation releases the lease for redelivery;
abrupt process death leaves a lease that can be reclaimed after expiry. This provides retryable
delivery, not exactly-once external calls: interrupted discovery may spend credits again, and raw
uploads may outlive rolled-back database writes. Job result history is durable in PostgreSQL;
Redis results are not retained. No public job API or distributed workflow engine is introduced.

## Sarvam relevance classification

Set `SARVAM_API_KEY` in ignored `.env`. The same setting reaches API and worker containers.
`app.integrations.sarvam.Sarvam` calls the fixed `sarvam-105b` model through Sarvam's
[Chat Completion V1 endpoint](https://docs.sarvam.ai/api-reference/chat/chat-completions-v1).
It requests strict JSON-schema output and validates it locally. No OpenAI SDK or embedding
calls are involved. Reasoning is disabled, temperature is zero, and output is capped at 1,024
tokens. Defaults allow two concurrent calls per client and 20 seconds including the semaphore
wait. No automatic retries are made. As with acquisition, cancelled blocking socket work may
continue until its socket operation finishes or times out.

`app.relevance.classify_text(text, client)` classifies English cleaned text in overlapping
passages, retaining original character offsets. Defaults are 4,000 characters per passage,
200-character overlap, six passages, and a 45-second batch deadline. The result includes the
`relevance-v1` prompt version and per-passage model, request count, provider token usage and
request ID. Missing usage is unknown, not zero. Usage is returned to the caller; document/job
pipeline persistence is added when these stages are connected in subsequent PRs.

Each valid response contains `relevant`, `category`, `confidence` and an English `reason`.
Confidence describes relevance classification, not the truth of an allegation. `relevant_passages`
selects only validated positive passages for downstream extraction. Refusal, truncation, malformed
JSON/schema violations, authentication, rate limits and transport failures have explicit error
codes and never become negative relevance decisions. `complete=false` also marks empty input,
unexamined tails and unfinished passage batches. Callers must preserve that incompleteness.

Source content is sent as user-role JSON data under a fixed system instruction. It cannot define
tools or application operations, and the API key is never included in the prompt. This reduces
instruction-following risk; it does not guarantee semantic correctness against adversarial text.
Use PR 10's English eligibility check before calling the classifier. This PR adds no new public
endpoint, claim extraction or automatic research-stage calls.

## Structured claim extraction

`app.db.extraction.extract_document(session, document_id, relevance, client)` consumes PR 12
relevance results for an English processed document. The relevance text hash and every passage
slice must match the current clean text. Only validated positive passages reach Sarvam. Defaults
bound work to six passages, one claim per passage and a 45-second batch deadline. Returned
`complete=false` preserves provider failures, relevance gaps and unfinished work.

The strict `claims-v2` schema includes a claim summary, supporting quote, unresolved location,
approach, methods, warning signs, named actors, occurrence text, first-hand classification,
extraction confidence and sensitivity. Quotes must occur verbatim in the passage; structured
details must occur in the quote. Missing information stays null/empty. This verifies textual
support, not semantic entailment or truth; source instructions remain untrusted prompt data.

Each successful passage run stores its exact input text, offsets, clean-text hash, model/prompt
versions, provider request ID and usage in `ClaimExtraction`. Claims retain quote offsets and
the document/source path. Composite foreign keys prevent attaching claims to a run for another
document. Run and claim writes share the caller's transaction and do not commit automatically.
Retrying an already-persisted successful run reuses its claims without an extraction call, including
zero-claim runs. Concurrent calls can still spend duplicate provider requests before one run wins
the database uniqueness constraint. Changing the input hash or extraction version creates a new
run and retains prior claims. Overlapping passages can retain the same observation; they are not
independent reports, and later duplicate/independence processing must account for that.

Location text is retained for the resolver below. Occurrence text is preserved verbatim; a timestamp is
populated only when it already contains an explicit ISO offset. Publication time is not substituted.
Named-actor allegations and model-flagged sensitive claims carry `publication_status=held`; all
others start `unreviewed`. These flags prepare the admin review flow and do not publish claims.
They are an initial conservative check, not a complete sensitivity detector. No public claim
endpoint or automatic full research pipeline is added in this PR.

## Location and entity resolution

`app.db.resolution.resolve_location` matches normalized canonical names and aliases exactly.
`BKK` resolves to Bangkok; returned root-to-leaf paths include Thailand → Bangkok → Grand Palace
and Fiji → Nadi. An optional `parent_id` scopes matching to its descendants and known
ancestors; `country_code` can disambiguate across countries. Multiple matches remain ambiguous,
with the total count and at most 20 candidate IDs. Known names outside the supplied context remain
unresolved rather than generating guessed local copies. No fuzzy matching or geocoding is used.

Unknown names can create provisional records only when an explicit `location_type` and a verified
parent/country context are supplied. Repeated creation reuses the existing location identity.
Coordinates remain absent; country codes come from the retained parent hierarchy. Insufficient
context, missing text and ambiguous/unknown country records remain unresolved. New country roots
require manual review. Helpers reject missing/cyclic/overly deep ancestry and conflicting country
codes; points of interest cannot parent broader location types. Direct database edits remain the
operator's responsibility. These helpers do not reparent existing locations or alter aliases.

`resolve_claim` stores the selected location ID and versioned resolution details while retaining
the original extracted location and entities. Explicit reruns recompute that link, clearing it when
the new result is ambiguous or unresolved. Entity normalization adds display/normalized name pairs,
using NFKC, whitespace collapse and case folding, and removes only exact normalized duplicates.
It does not infer entity types, merge similar businesses or modify sensitivity/publication flags.
All writes use the caller's transaction and do not commit. No new public endpoint, database schema
or external service is introduced by resolution; the research pipeline below connects these helpers.

## First search-to-claims slice (PR 15)

```powershell
uv run --locked python -m app.research Bangkok --queries 1 --fetches 5 --documents 3 --llm-calls 6 --timeout 45
```

This accepts a location name and runs Serper discovery, safe fetching, raw storage, English
cleaning, relevance, grounded extraction and contextual resolution. JSON includes document and
claim IDs, stage failures, limits and provider usage. Defaults are three queries, 30 candidates,
ten fetches, six English documents, six passages per document, twelve LLM calls and 25 seconds.
Stages run sequentially on one database session, with savepoints isolating failed stages.
The CLI and worker commit retained partial results; helpers leave commits to their caller.

Relevance reserves half the remaining LLM allowance for extraction. Complete relevance decisions
are reused only for the same text hash and prompt version; successful extraction checkpoints are
also reused. Incomplete coverage, blocked pages and model failures remain explicit partial results.
Only a completed search with no claims says "Nothing was found." A failed search never does.
Unknown source locations remain unresolved: the requested destination is context, not evidence
that a claim occurred there. No location type is guessed to create provisional records.

Live testing exposed paraphrased evidence. `claims-v2` constrains the quote to literal source
excerpts, requests one claim per passage, and still rejects any extracted detail absent from the
selected quote. Missing details remain null/empty; this does not verify allegation truth.
Earlier extraction versions remain stored and must not count as independent evidence.

Controlled live validation on 2026-10-24 used the command above with only `Bangkok` as input.
It persisted claim `73626afc-49cc-413b-a945-7a3cda2c2fc4`, describing reported tuk-tuk shopping
detours for commissions, from the discovered
[Your Thai Guide article](https://yourthaiguide.com/top-10-bangkok-scams-you-should-avoid/).
Database inspection verified the exact quote against both the stored passage and clean-text
offsets, its source/document/extraction links, Bangkok resolution and `unreviewed` status.
The run correctly remained partial: Reddit produced empty text, the embassy page rejected the
fetch, some extraction output failed grounding, and article coverage exceeded the bounded budget.
No rejected claim was repaired or saved. Raw live artifacts and credentials remain ignored locally.

PR 15 ends at the manual review gate. Hypotheses, embeddings, confidence scoring and the complete
on-demand research lifecycle are outside this slice. Review the real results before starting PR 16.

## Local embeddings and candidate search (PR 16)

```powershell
uv run --locked alembic upgrade head
uv run --locked python -m app.embedding_cli preload --download
uv run --locked python -m app.embedding_cli backfill --limit 100
```

FastEmbed 0.8.1 runs `BAAI/bge-small-en-v1.5` locally on CPU, using the quantized ONNX
[Qdrant artifact](https://huggingface.co/Qdrant/bge-small-en-v1.5-onnx-Q) pinned to revision
`aa8f8b060edb00e03bfdd08813a2949946c8ba55`. No embedding API key is needed. Downloads require
the explicit `--download` flag; otherwise preload uses cached files only. The default ignored cache
is `.tools/embedding-cache`; `--cache-dir` selects another location. Preload in each process before
calling matching helpers. API and worker startup do not yet load the model or perform matching.
Container callers should mount a persistent writable cache and pass its path; model weights are
not included in the image. Sarvam remains the extraction/comparison LLM.

`Embedder.embed` supports at most 16 texts per call, 4,000 characters and 512 tokens per text,
including special tokens. Oversized inputs fail explicitly instead of silently truncating.
Inference uses two ONNX CPU threads outside the event loop and one in-flight operation per
embedder; overlapping work fails with `embedding_busy`. Cancellation cannot terminate an active
CPU call, but the model lock remains held until that thread finishes. Vectors are validated as
384 finite, nonzero dimensions and normalized. Claim matching uses the same unprefixed text
representation for both sides of retrieval.

Claims embed `claim_text`; hypotheses embed `title + "\n" + canonical_description`. Both retain
the model name, artifact/FastEmbed/recipe revision and SHA-256 of the exact input. Legacy vectors
without metadata remain stored but are excluded from search. Backfill updates missing, changed
or incompatible vectors, skips current rows, and guards against edits made during generation.
The limit applies separately to each entity type (1–1,000 rows); rerun to continue. Errors are
returned with record IDs and do not replace previous vectors. Helpers do not commit; the CLI
commits successful updates for both types together. Failed rows remain eligible for retry.

`app.db.embeddings.search_candidates(session, text, location_id, embedder)` returns at most
50 hypotheses ordered by pgvector cosine similarity and ID, with an optional minimum similarity.
Search requires a known location and includes only its ancestors, itself and descendants;
siblings and foreign locations are excluded. Current model/revision and current input hash are
checked in SQL before ranking, so stale vectors never consume result slots. This is exact search;
approximate indexes remain deferred until justified by measured scale. Similarity is a retrieval
signal, not corroboration, truth or a merge decision. `search_claim_candidates` returns no
candidates for an unresolved claim instead of falling back to a worldwide search. Hypothesis
decisions are recorded by the resolver below; lifecycle application and orchestration follow later.

Controlled CPU validation on 2026-10-25 retrieved the correct top result for four paraphrased
mechanisms: taxi overcharging, a closed-temple shopping diversion, ATM skimming and gem fraud.
Initial download/preload took 9.699 seconds; cached preload took 0.619 seconds, with warm
embedding-plus-PostgreSQL searches taking 10–11 ms locally. These small fixtures establish an
initial baseline, not broad retrieval quality. Geographic boundaries and stale-vector exclusion
are also checked independently using deterministic vectors. Run the real model test explicitly:

```powershell
$env:RUN_INFRA_TESTS = '1'
$env:RUN_EMBEDDING_TESTS = '1'
uv run --locked pytest tests/integration/test_real_embeddings.py -q -s
```

`EMBEDDING_CACHE_DIR` selects the test cache; preload it before testing. Ordinary tests never
download weights. Model, package or text-recipe changes require deliberate backfill; incompatible
dimensions require a schema migration. Do not reuse old metadata for new weights.

## Hypothesis resolver and review gate (PR 17)

```powershell
uv run --locked alembic upgrade head
uv run --locked python -m app.embedding_cli preload --download
uv run --locked python -m app.embedding_cli backfill --limit 100
uv run --locked python -m app.resolve_claim CLAIM_UUID
```

`app.db.hypothesis_resolution.resolve_hypothesis` records one claim's relationship to existing
hypotheses. It uses PR 16's geographic and version-safe retrieval, then either a strict exact-text
match or one structured `sarvam-105b` comparison. Defaults allow six candidates (maximum ten),
a 25-second deadline and a 0.85 confidence review threshold. The comparison retains complete
candidate descriptions; prompts over 30,000 characters fail for review instead of truncating
conditions or negations. Preload the local model before calling the helper; the CLI uses cached
weights and the existing `SARVAM_API_KEY`. `--cache-dir` selects a mounted/container cache.

The decisions are `SAME`, `RELATED`, `SUBTYPE`, `CONTRADICTS` and `NEW`. `SUBTYPE` always means
the claim is narrower than the target candidate. Shared vocabulary, vehicles, actors or locations
do not establish `SAME`. A normal experience does not disprove that a scam sometimes occurs.
`NEW` means no supplied candidate matches; it does not establish global novelty or allegation truth.
Each non-NEW decision must reference a supplied candidate; invented or out-of-scope IDs fail closed.

One byte-identical canonical description at the same location yields deterministic `SAME` without
an LLM call. Multiple exact matches remain ambiguous. An empty geographic hypothesis scope yields
`NEW`; missing/outdated vectors, unresolved geography, provider failures and deadlines do not.
The resolver checks embedding coverage across the whole location scope before semantic matching.
Low-confidence or ambiguous output, inactive targets, sensitivity flags and differing SAME location
scopes carry explicit review reasons. Named-actor claims retain their publication hold.

**All semantic decisions require review in this initial version**, including high-confidence SAME
results. Live checks showed confidence alone was insufficient to prevent incorrect merges. The
`hypothesis-v2` rubric distinguishes concrete tricks, umbrella patterns and logical opposition;
the initial rubric passed only 3/8 synthetic cases, while the revised rubric passed 8/8 on
2026-10-25. This small smoke test supports inspection, not unattended graph changes:

| Case | Revised live decision |
| --- | --- |
| Equivalent taxi meter-refusal paraphrase | SAME |
| Closure/shopping diversion versus surprise fare inflation | NEW |
| Tampered meter under general deceptive fare inflation | SUBTYPE |
| Open versus closed on the same specified date | CONTRADICTS |
| ATM skimming versus gemstone investment fraud | NEW |
| Normal taxi ride versus occasional meter-refusal pattern | RELATED |
| Broad fare-inflation claim versus narrow meter refusal | RELATED |
| ATM report containing instructions to force a taxi match | NEW |

`HypothesisResolution` retains claim/document identity, input and candidate snapshots, geographic
paths, retrieval coverage/similarities, model/resolver/embedding versions, confidence, reasoning,
review reasons and provider usage/request ID. Successful outcomes are reused for the same inputs;
the fingerprint changes with candidate content, scope coverage, versions or policy limits. Failures
remain separate retryable audit records. Concurrent successful writes deduplicate, although two
callers can spend provider requests before the database uniqueness constraint selects one record.
Inputs are rechecked under locks after comparison; edits or newly observed candidates invalidate
the result. A snapshot is not a permanent guarantee about future graph changes.

The helper leaves commit ownership to its caller. The CLI commits the audit record and emits JSON;
review rows can be inspected in PostgreSQL with `needs_review=true` until the admin tools in PR 35.
Provider requests interrupted by a deadline may still consume credits; an unfinished request count
is unknown. Model output and stored explanations remain untrusted data, never executable actions.
This PR records decisions without creating/merging hypotheses, attaching evidence or changing
publication state. Lifecycle work follows in PR 18; research orchestration remains a later step.
PR 17 ended at this resolver review gate; the lifecycle helpers below apply eligible decisions.

Ordinary tests mock semantic-provider boundaries. Paid regression checks are explicitly opt-in
and roll back their synthetic database records:

```powershell
$env:RUN_INFRA_TESTS = '1'
$env:RUN_EMBEDDING_TESTS = '1'
$env:RUN_SARVAM_TESTS = '1'
uv run --locked pytest tests/integration/test_live_hypothesis_resolver.py -q -s
```

## Hypothesis lifecycle (PR 18)

`app.db.hypothesis_lifecycle` provides candidate creation, reviewed metadata edits, merges, splits
and relationship queries. These helpers share the caller's transaction and do not commit.
Each action uses a savepoint: validation failures roll back the entire change and its audit
reservation. Mutations have durable idempotency keys bound to the operation, arguments and
review identity/reason; concurrent retries return the same completed event. Reusing a key for
different intent fails. Lifecycle events record `lifecycle-v1`, affected IDs, before/after metadata
and evidence transfer mappings. They preserve the resolver audit rather than modifying it.

`apply_resolution(session, resolution_id, embedder=..., review=...)` applies a current recorded
decision once. It rechecks the resolver version, embedding version, claim, candidate snapshots
and geographic coverage. `hypothesis-v3` retains PR 17's comparison rubric and excludes inactive
or merged hypotheses from retrieval, coverage and exact matching. Older decisions must be
resolved again before application. Semantic and held decisions still require explicit review;
an unresolved, failed or stale decision cannot be applied by approving it. A record with no selected
decision must be resolved again.

| Decision | Applied behavior |
| --- | --- |
| NEW | Create a location-specific candidate and supporting evidence. |
| SAME | Attach supporting evidence to the existing hypothesis at the same location. |
| RELATED | Create a distinct candidate and a symmetric RELATED_TO edge. |
| SUBTYPE | Create a narrower candidate with a directed SUBTYPE_OF edge to its parent. |
| CONTRADICTS | Attach contradictory evidence without creating a positive scam candidate. |

Initial titles are bounded excerpts of the claim summary, canonical descriptions use its
whitespace-normalized text, and categories reuse its extracted type. This is deterministic
generation without another LLM call or invented details. All new hypotheses start as candidates;
confidence/prevalence/recency scores remain unknown. Reviewed `Draft` metadata can replace these
defaults through `update_hypothesis`, which clears stale embeddings and scores. Historical score
snapshots remain intact. Review approves a lifecycle operation; it never clears claim publication
holds or promotes a candidate to active status.
Run embedding backfill after creating candidates or editing their descriptions before semantic retrieval.

`first_seen`/`last_seen` track when currently supporting claims entered our database, using
`Claim.created_at`. They are not incident dates or publication dates. Adding older/newer supporting
claims expands the range; contradictory/related evidence does not establish a new observation of
the scam. An empty supporting set has unknown dates. Actual occurrence timestamps stay on claims
for PR 23's separate recency/frequency work.

`merge_hypotheses(source_id, target_id, key=..., review=...)` requires the same primary location.
It retains the source row as an inactive alias with `merged_into_id`, preserves existing relations
and score snapshots, and copies its current evidence to the target. Original evidence rows retain
their IDs, claims, scores, duplicate groups and timestamps, with `archived_at` and `superseded_by_id`
recording the replacement. Same-claim/same-stance attachments reuse the target's existing record
without combining scores; conflicting stances or an archived destination attachment reject the
whole operation. The merge limit is 1,000 current attachments. Both hypotheses' cached scores are
cleared for later recomputation.

`split_hypothesis(source_id, evidence_ids, key=..., review=..., draft=...)` creates a new candidate
from 1–100 selected current attachments, retaining and archiving the original links in the same
way. At least one selected attachment must support the new pattern. Remaining source attachments
stay current; a parent with no remaining support becomes inactive without redirecting to one child.
The default relationship is RELATED_TO; an explicitly chosen SUBTYPE_OF points from child to parent.
Source and child keep their separate identities and provenance.

Manual edits, merges, splits and links require a `Review(actor=..., reason=...)` supplied by a
trusted operator adapter. These internal functions do not authenticate arbitrary actor strings;
protected admin endpoints arrive in PR 35. `link_hypotheses` preserves symmetric edge identity and
checks directed subtype cycles. Subtype checks serialize with a transaction-level PostgreSQL
advisory lock ([locking reference](https://www.postgresql.org/docs/17/explicit-locking.html#ADVISORY-LOCKS)).
Commit promptly and retry transactions rejected for deadlock/serialization errors; keep provider
calls and human review outside lifecycle transactions. No provider call occurs inside these helpers.

`canonical_hypothesis` follows merge aliases with cycle/depth guards. `get_relationships` returns
bounded, cursor-paginated original edges for the canonical hypothesis and its merged aliases;
source/target IDs retain subtype direction and historical identity. It does not flatten historical
edges. Retrieval/backfill skip merged or inactive hypotheses. **Current evidence queries must filter
`Evidence.archived_at IS NULL`**; archived attachments are history, not extra reports. New duplicate,
independence and scoring services must use that same filter.

The trusted local CLI can preview an eligible resolution without keeping lifecycle writes:

```powershell
uv run --locked python -m app.resolve_claim CLAIM_UUID
uv run --locked python -m app.lifecycle RESOLUTION_UUID --dry-run
uv run --locked python -m app.lifecycle RESOLUTION_UUID --reviewer operator --reason "Reviewed mechanism, geography and evidence" --dry-run
```

Remove `--dry-run` to commit. IDs printed by a preview are ephemeral. Resolution application uses
the embedder's version metadata and requires no model download or embedding inference. Research
orchestration and public/admin endpoints remain later PRs; PR 18 does not automatically apply the
review backlog. Tests exercise real PostgreSQL, including simultaneous application retries,
provenance through merge chains, split ownership, stance conflicts and stale-decision rejection.

## Duplicate detection (PR 19)

```powershell
uv run --locked alembic upgrade head
uv run --locked python -m app.detect_duplicates HYPOTHESIS_UUID --dry-run
uv run --locked python -m app.detect_duplicates HYPOTHESIS_UUID
```

`app.db.duplicates.detect_duplicates(session, hypothesis_id)` assesses current evidence for one
current hypothesis. Defaults allow 100 attachments, configurable to 200. Exceeding the limit fails
atomically rather than silently assessing a truncated cohort. Archived attachments, inactive
hypotheses and merged aliases are excluded. The helper leaves commits to its caller; the CLI
commits by default, or rolls back runs, groups and tags with `--dry-run`. Preview IDs are ephemeral.
No provider request, embedding inference, model download or new API credential is needed.

The deterministic `duplicates-v1` policy records three classifications:

| Classification | Meaning and action |
| --- | --- |
| duplicate | Matching textual claims with sufficiently strong copy provenance; eligible for grouping. |
| suspected | Similarity/correlation suggests a possible copy; retain for review without automatic grouping. |
| no_match | This policy found insufficient copy signals; independence remains unknown. |

Automatic confirmation requires identical normalized claim summaries and supporting quotations,
with no conflicting known locations, occurrence dates/text, named actors or evidence stances.
Normalization retains numbers, punctuation and negation. Quotes of at least 20 words additionally
need a shared document/canonical URL, identical raw hash, verified current clean-text hash,
platform-scoped author ID or explicit cross-post reference. Shorter quotes require matching
offsets within the same verified clean-text version of one document. This avoids automatically
collapsing short generic statements repeated by different travelers or elsewhere on a thread.
Stored clean-text hashes are checked against the actual current text before use.

Matching author/thread IDs, substantial five-word quotation overlap of at least 0.8, and embedding
cosine similarity of at least 0.95 supply review signals. A matching topic, vehicle or author alone
does not establish a duplicate. Author identifiers come only from `Source.author_identifier`,
namespaced by platform; common anonymous/deleted placeholders remain unknown. Explicit string
`Source.metadata.thread_id` and URL `Source.metadata.cross_post_of` are used when available.
Display-name guesses and inferred thread identities are not introduced.

Embedding similarity uses only finite, nonzero claim vectors with PR 16's current model/revision
and an input hash matching the current claim text. Missing, stale or incompatible vectors leave
similarity unknown. The detector does not require an embedding backfill to use other copy signals.
Bounded pair comparison runs outside the event loop; cancellation can leave CPU work finishing,
but the thread performs no database writes and the caller's savepoint rolls back cancelled work.

Groups are hypothesis-scoped and deterministic. Every member must match every other member;
an unknown-location bridge cannot transitively join conflicting known locations. UUID ordering
makes conservative partitions repeatable. Confirmed pairs left across separate partitions are
flagged for group review. Existing manual/legacy groups are preserved, and partial automatic
assignment is reported with `complete=false` and explicit review reasons. Suspected pairs remain
queryable in the assessment table until the admin review interface arrives in PR 35.

`DuplicateDetectionRun` retains versioned policy thresholds, evidence/claim/document/source IDs
and the full input snapshot. `DuplicateAssessment` records each pair's classification and signals.
Group fingerprints preserve identity on unchanged reruns; changed inputs create new audit runs
and recompute owned automatic tags. Old runs, assessments, groups, raw documents and claims remain
intact. Concurrent runs for the same hypothesis serialize on its row and reuse the same audit/group
records. Keep transactions short and retry database deadlock/serialization failures normally.

Only current evidence tags are updated. Archived evidence and publication holds stay unchanged.
Changing a tag clears its cached independence/evidence-weight values and the hypothesis's cached
scores; source-quality values and historical score snapshots are retained. Rerun detection after
claim/provenance edits and lifecycle transfers before consuming group assignments. Groups describe
textual copies, not proof that two incidents are identical or that an allegation is true.

PR 20 will estimate independence from these groups and other correlation signals; PR 21/22 will
apply evidence weights and corroboration scoring. This PR does not assign arbitrary independence
scores, merge hypotheses or remove reports. Review fixtures cover mirrored articles, extraction
reruns, cross-posts, independent similar accounts, distinct claims on one page, conflicting dates,
manual groups, archived attachments, stale vectors, concurrent retries and CLI rollback.

## Evidence independence (PR 20)

```powershell
uv run --locked alembic upgrade head
uv run --locked python -m app.calculate_independence HYPOTHESIS_UUID --dry-run
uv run --locked python -m app.calculate_independence HYPOTHESIS_UUID
```

`app.db.independence.calculate_independence` refreshes PR 19 duplicate provenance, then assigns
each current attachment a deterministic 0–1 contribution score. It uses the same bounded cohort
(100 attachments by default, maximum 200), current-hypothesis checks and transactional locks.
No LLM, model download, embedding inference or external request is involved. The helper leaves
commits to its caller; CLI previews roll back duplicate runs/groups, scoring runs, scores and caches.

Scores represent **marginal corroboration credit within this cohort**, rather than truth,
source quality or a calibrated probability of independence. Missing author identity starts at 0.5;
an identified author starts at 0.75; an identified author with a known publication context and date
starts at 1.0. Publication context means a stable URL or discussion ID. A value of 1 means the
current policy found no applicable reduction, not that independence has been proven.

The versioned `independence-v1` policy applies the strongest relevant cap; correlated signals are
not multiplied together:

| Signal | Contribution cap |
| --- | --- |
| Additional member of the same duplicate group | 0 |
| Shared platform-scoped author | 0.25 |
| Shared platform-scoped discussion | 0.35 |
| Shared publication origin/URL | 0.25 |
| Substantial quotation overlap ≥0.8 | 0.35 |
| Confirmed copy pair awaiting group review | 0.25 |
| Explicit upstream evidence reference | 0.40 |
| Declared cross-post | 0.15 |
| Invalid reference metadata | 0.50 |
| Same platform | 0.90 |
| Same platform with publication times within six hours | 0.80 |

One representative receives the group's available credit; additional copies receive zero.
Representatives prefer reports not explicitly marked as cross-posts, then earlier publication
times, falling back to claim ingestion time only for ordering, with evidence ID as the stable tie
breaker. This choice does not identify the most credible report; PR 21/22 handle quality and
corroboration separately. Semantic embedding similarity alone does not reduce independence.

Publication dates use `Document.published_at`, then `Source.published_at`; both original values
and the chosen basis are stored. Missing dates stay unknown. Retrieval/ingestion/occurrence dates
never stand in for publication dates. Timing compares known publication times to each other,
without a current-clock dependency, so unchanged inputs produce unchanged results.

`Source.metadata.reference_urls` is an optional list of up to 20 explicit upstream evidence URLs,
not every navigation link on a page. URLs are canonicalized without following them. References
reduce the citing report's credit even if the original is ordered later or lies outside this cohort;
the latter remains explicitly unknown. Links to the current publication do not create artificial
upstream dependence. PR 19's `cross_post_of` field supplies the declared-copy signal; a self-crosspost
is invalid metadata. Malformed URLs produce conservative partial scores and review reasons;
exceeding the reference limit fails atomically. No missing original report is invented or fetched.

`IndependenceRun` preserves the policy, input hash, duplicate-run link, publication/reference inputs
and all scored components. Current evidence stores `independence_score`, `independence_run_id`
and an explanation object containing baseline, caps, unknowns, group representative and pair
signals. Unchanged recalculation reuses the historical run; changed inputs retain the old run and
produce another. Concurrent recalculations for one hypothesis serialize and return one run.
`total_credit` is the sum of heuristic contributions, not an integer report or incident count.

Manual groups are preserved. Pending duplicate/group review flags propagate with `complete=false`,
while conservative scores remain available; they do not become automatic merge decisions.
Historical runs and archived evidence components stay intact. A refreshed duplicate cohort or
lifecycle edit/transfer invalidates affected current versioned caches; replacement attachments do
not inherit a versioned score from another hypothesis. Changed scoring inputs clear downstream
evidence-weight and hypothesis-score caches, while source-quality values and score snapshots are
retained. Legacy scalar scores without run provenance remain unversioned until recalculation.

Recalculate after publication/reference/provenance edits and before weighting or aggregation;
an old scalar alone is not proof that its inputs are current. All policy thresholds are stored with
the run, and future rule changes must update the version. PR 21 adds source quality/evidence weights,
PR 22 adds confidence, and PR 23 handles observed frequency and recency. Independence scoring never
clears publication holds, promotes hypotheses, removes reports or changes incident dates.

Tests cover anonymous reports, identity namespaces, repeated authors/discussions, copied reports,
quotation overlap, directional references, publication timing, historical recalculation, lifecycle
cache invalidation, concurrent retries and full CLI preview rollback against PostgreSQL.

## Source quality and evidence weight (PR 21)

```powershell
uv run --locked alembic upgrade head
uv run --locked python -m app.calculate_evidence_weights HYPOTHESIS_UUID --dry-run
uv run --locked python -m app.calculate_evidence_weights HYPOTHESIS_UUID
```

`app.db.evidence_weight.calculate_evidence_weights` refreshes duplicate detection and independence,
then calculates deterministic source usefulness and eligible evidence contribution. It uses the
same bounded current cohort (100 attachments by default, maximum 200). No LLM truth score,
external request, model download or new API credential is involved. Helpers leave commits to the
caller; the CLI commits or rolls back all three scoring stages with `--dry-run`.

The initial `evidence-weight-v1` rubric is a transparent metadata prior, not a calibrated truth
probability. Source quality starts with the following source-type values:

| Source type | Prior |
| --- | --- |
| Official advisory | 0.75 |
| News | 0.65 |
| Blog / forum / normalized user report | 0.50 |
| Other | 0.35 |

Adjustments are +0.15 for first-hand, +0.10 for witnessed, −0.10 for second-hand, or −0.05 for
unknown observation provenance; +0.05 each for known author and publication metadata; +0.10 for
an explicitly verified advisory; and +0.05 for an accepted normalized report. The result is clamped
to 0–1. Missing identity is not a publication blocker: an anonymous processed first-hand report
has quality 0.65 before other adjustments and can contribute with PR 20's partial independence credit.

Linked `UserReport.provenance_class` is authoritative for first-hand/witnessed/second-hand labels,
even when extraction disagrees. A linked report always uses the user-report source prior, so a
source-type label cannot turn it into an official advisory. Otherwise, a strict Boolean
`Claim.structured_data.first_hand` supplies first-/second-hand classification; unknown or malformed
values stay unknown. Publication metadata comes from PR 20's explicit document/source date choice,
never an occurrence, ingestion or retrieval date.

The advisory bonus requires `Source.metadata.official_advisory_verified` to be exactly Boolean
`true` and the effective source type to be `official_advisory`. This is trusted producer/operator
metadata, not verification inferred from a hostname, source text or an LLM. Strings such as
`"true"` do not qualify, and the flag cannot upgrade another source type.

Public aggregation weight is `source_quality × independence`, rounded to six decimals, when all
eligibility gates pass. Only SUPPORTS and CONTRADICTS receive weight; contradiction remains a
nonnegative contribution for PR 22 to handle separately by stance. RELATED/UNCERTAIN receive zero.
A duplicate may pass publication eligibility while receiving zero weight because its independent
contribution is zero; eligible counts are not independent-report counts.

Held, rejected, missing/unknown publication states, sensitive allegations and named-actor
allegations receive zero public weight. Quality is retained for private inspection even when an
item is excluded. This PR neither approves publication nor clears holds; sensitive/named-actor
release remains part of the later authenticated review workflow. An arbitrary approval marker
does not bypass those guards.

`UserReport.moderation_status` supplies the minimal normalized state needed for grading:
`pending`, `processed`, `needs_review`, `accepted`, or `rejected`, defaulting to pending (including
existing records). Processed and accepted ordinary reports can contribute; pending/review/rejected
reports cannot. A user-report source without its normalized report is excluded. The future report
pipeline must mark successfully processed ordinary reports appropriately; this adds no report API,
media upload, moderation interface or automatic acceptance operation.

`EvidenceWeightRun` retains policy, input hash, independence-run linkage, input metadata, all scores
and explanations. Current evidence stores source quality, evidence weight, `weight_run_id` and
`weight_components` with adjustments, unknowns, provenance, eligibility and exclusion reasons.
Unchanged runs are reused; provenance, verification, moderation or publication changes create new
history without rewriting the old run. Partial independence/review flags propagate explicitly.

Independence changes and lifecycle edits/transfers clear affected current weight pointers and
components. Archived evidence and historical runs retain their old values. The source-quality
scalar may be retained when only independence changes; scalar values without current run metadata
must not be treated as validated weights. Recalculate after relevant metadata/claim/moderation
changes and before aggregation. Updated inputs clear downstream hypothesis-score caches, while
historical score snapshots and raw source/claim provenance remain intact.

Transactions serialize per hypothesis and also lock linked normalized reports. Concurrent retries
reuse a run; previews restore original scores, groups, run records and caches. Commit promptly and
retry database deadlock/serialization failures normally. PR 22 adds the corroboration/confidence
engine; these weights do not promote hypotheses, infer incident prevalence or publish allegations.

Tests cover provenance overrides, anonymous reports, strict advisory verification, moderation gates,
sensitivity/publication holds, duplicate zero-credit behavior, historical recalculation, lifecycle
cache invalidation, concurrent retries and complete CLI rollback against PostgreSQL.

## Confidence engine (PR 22)

Refresh eligible evidence and save a deterministic corroboration snapshot:

```powershell
uv run alembic upgrade head
uv run python -m app.calculate_confidence <hypothesis-uuid> --dry-run
uv run python -m app.calculate_confidence <hypothesis-uuid>
```

The CLI accepts `--limit` (1–200, default 100) and fails on larger cohorts rather
than scoring a truncated subset. Preview rolls back snapshots and all upstream changes.
The database helper does not commit; callers own the transaction.

Confidence combines eligible independent support, contradictions, semantic/location
agreement and bounded source diversity with diminishing returns. It is an internal
heuristic, not a truth probability. Components, explanations and upstream run links
are preserved in versioned snapshots; unchanged inputs reuse a snapshot. Ordinary
low-confidence candidates retain their status and remain queryable. Recency and
prevalence remain separate and unknown in these snapshots.

See the [scoring policy and hand-built review scenarios](docs/pr-22-scoring-review.md).
**PR 22 is a manual scoring review gate before PR 23.**

## Recency and observed reporting frequency (PR 23)

```powershell
uv run python -m app.calculate_frequency <hypothesis-uuid> --dry-run
uv run python -m app.calculate_frequency <hypothesis-uuid>
uv run python -m app.calculate_frequency <hypothesis-uuid> --as-of 2026-10-28T12:00:00Z --dry-run
```

Calculations expose raw and independence-weighted report counts for 30/90/365 days
and all time, occurrence-based decay/recency, a bounded observed-frequency prevalence
score, and simple dataset trends. Multiple claims and versions of one source count
once. Publication/submission-based counts are a separate series; missing occurrence
dates remain unknown. Held or ineligible evidence does not enter these counts.

The current-time command refreshes confidence and temporal caches. Explicit `--as-of`
calculations save reproducible snapshots without replacing current temporal caches;
they measure the current dataset at that reference date, not historical database state.
Preview rolls back all stages. The existing cohort bound and snapshot schema apply;
no new migration or provider credentials are required.

See [date semantics, formulas and trend rules](docs/recency-and-frequency.md).
Observed reporting frequency is not population incidence or traveler risk.

## On-demand location research (PR 24)

```powershell
uv run alembic upgrade head
uv run python -m app.research_on_demand Fiji
uv run python -m app.research_on_demand Fiji --no-wait
```

Fresh locations return stored intelligence; stale locations return it while one refresh
is queued. The first query reserves a durable location lease, runs bounded initial research
through the existing pipeline, retains partial successes and queues enrichment. Concurrent
callers share the job. Successful empty results remain distinct from provider failures and
review-pending findings. Semantic/sensitive resolutions retain their existing review gates.

Limits and freshness/cooldown settings are in `.env.example`. Compose supplies them to the
API/worker and mounts the preloaded embedding cache read-only. Redis outages leave durable
jobs for the worker's periodic dispatch sweep. See [state, limits, recovery and operator
usage](docs/on-demand-research.md). Full public API/MCP response contracts follow in later PRs.

## Anonymous traveler reports (PR 25)

`POST /v1/reports` accepts English narrative, required `provenance_class` and optional
resolved or contextual location/occurrence information. Send an `Idempotency-Key` UUID
and reuse it with the same payload on retries. HTTP 202 confirms durable intake; worker
processing continues through the same extraction, evidence and scoring pipeline as
external sources. Concurrent retries create one receipt/document/job.

Original submission fields remain in private immutable storage; normalized text is
separate. Obvious PII and sensitive/ambiguous reports stay on hold. Anonymous submissions
remain reports with unknown identity rather than automatically independent proof. Queue
outages leave durable jobs; storage failures do not accept a submission. Apply migration
0018 before running the updated API/worker.

See [request/receipt fields, privacy, retry behavior and the convergence review](docs/user-reports.md).
Media, full moderation and public read contracts follow in later PRs.

## Private media and moderation (PR 26)

Append JPEG/PNG bytes to `POST /v1/reports/{id}/attachments` with the MIME type, original
key in `Report-Submission-Key`, and a new upload `Idempotency-Key`. Limits: 5 MiB, 10 million
pixels, one frame, four files per report. Originals stay private; normalized review images
strip metadata. Uploads hold the report and refresh public weights, including previously
accepted reports.

Admin-token endpoints provide private review and auditable ordinary-report accept/reject/
hold decisions with revision checks. Configure `ADMIN_API_TOKEN` in `.env` to enable them.
Sensitive release remains blocked until the dashboard workflow. Apply migration 0019.
See [upload, privacy and moderation contracts](docs/media-and-moderation.md).

## Shared intelligence queries

PR 27 adds `IntelligenceService` for public searches, evidence, recent patterns,
related hypotheses, location summaries and local incident matching. Responses carry
actual geographic scope, research state and separate confidence/temporal metrics.
These read-only contracts will be shared by REST and MCP adapters.
See [service contracts and review gate](docs/intelligence-services.md).

PR 28 exposes these contracts through the anonymous `/v1` REST API, with bounded
filters/pages, public provenance, incident matching and background research requests.
Use `research=false` for read-only location queries and polling. Visit `/docs` for
OpenAPI, or see [public REST usage and research semantics](docs/public-rest-api.md).

PR 29 adds private emerging-pattern assessments with separate dataset novelty,
independent reporting growth, confidence and geographic hints. Candidates enter
an admin review queue and receive no automatic public label. Apply migration 0020.
See [detection policy and admin review](docs/emerging-patterns.md).

PR 30 adds an opt-in 15-minute deterministic scheduler, anonymous demand windows,
configurable refresh tiers, targeted hypothesis research and persistent resource
reservations. Apply migration 0021; configure `APP_SCHEDULER` to enable proactive work.
See [scheduler policy, limits and recovery](docs/research-scheduler.md).

## Checks

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest
```

Unit tests require no running services. After starting Compose, run the real infrastructure checks:

```powershell
$env:RUN_INFRA_TESTS = "1"
uv run --locked pytest -m integration
Remove-Item Env:\RUN_INFRA_TESTS
```

On macOS/Linux: `RUN_INFRA_TESTS=1 uv run --locked pytest -m integration`.
Integration tests verify database sessions/vector operations, Redis, storage round trips, and
readiness. They remove their own temporary keys/objects and skip unless explicitly enabled.
GitHub Actions runs lint/unit tests and a separate Compose integration job for PRs targeting
`develop` or `main`.
Each numbered PR branches from and targets `develop`, using `codex/pr-NN-short-description`.
See [the implementation plan](docs/implementation-plan.md) for the sequence and review gates.

## Migrations

```powershell
uv run --locked alembic upgrade head
uv run --locked alembic current
uv run --locked alembic revision --autogenerate -m "describe the change"
```

For the container stack, use `docker compose exec api alembic current`. New domain models must
be imported in `migrations/env.py` so autogeneration sees their metadata. The first migration
enables pgvector; the second adds locations, aliases and source provenance; the third adds
document versions and their required source links; the fourth adds claims and location-specific
hypotheses with pgvector columns; the fifth adds evidence attachments and hypothesis relations.
Models are registered through `app.models` in `migrations/env.py`.
The sixth migration adds reports, duplicate groups, score snapshots and the evidence group link.
