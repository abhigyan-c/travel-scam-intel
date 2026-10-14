# Travel Scam Intelligence

An evidence-backed travel scam intelligence backend. The repository currently provides the
API and local infrastructure; domain models, research, and ChatGPT integration arrive in later PRs.

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
database column `metadata`. Public source-write endpoints and document versions are deferred.

Responses include `X-Request-ID`. A supplied ID is accepted only if it is 1–64 ASCII characters,
starts with a letter or digit, and uses letters, digits, `.`, `_`, or `-`; otherwise a new ID is
generated. Errors use `{"error":{"code":"...","message":"...","request_id":"..."}}`,
with field locations/types added for validation failures. Logs are JSON and include request IDs,
method, path, status and duration, without request bodies, headers or raw query strings.

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
enables pgvector; the second adds locations, aliases and source provenance.
