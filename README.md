# Travel Scam Intelligence

An evidence-backed travel scam intelligence backend. The repository currently provides the
FastAPI foundation; research, persistence, and ChatGPT integration arrive in later PRs.

## Local development

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```powershell
uv sync --locked
Copy-Item .env.example .env
uv run --locked uvicorn app.main:app --reload --no-access-log
```

uv uses Python 3.12 from `.python-version` and the committed dependency lockfile. On macOS/Linux,
use `cp .env.example .env` to copy the settings. No external credentials or infrastructure are
required yet. Keep local credentials in the ignored `.env`; never commit real values.

Settings: `APP_NAME` (default `Travel Scam Intelligence`), `APP_ENVIRONMENT`
(`local`, `test`, or `production`), and `APP_LOG_LEVEL` (default `INFO`). Environment variables
override `.env`. Unknown `.env` keys are ignored so later service credentials can coexist.

- `GET /`: application name and version.
- `GET /health`: process liveness (`{"status":"ok"}`); dependency readiness will arrive later.
- `/docs`: interactive API documentation.

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

GitHub Actions runs the same checks for PRs targeting `develop` or `main`.
Each numbered PR branches from and targets `develop`, using `codex/pr-NN-short-description`.
See [the implementation plan](docs/implementation-plan.md) for the sequence and review gates.
