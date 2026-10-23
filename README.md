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
provisional flag; coordinates are optional and are not inferred. Provisional creation from
research and deeper hierarchy validation arrive with location resolution in PR 14.

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
