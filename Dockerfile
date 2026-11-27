FROM python:3.12-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS base
# Apply vendor security updates newer than the upstream Python image. Capture the
# final image digest and rescan on every release; a pinned base alone is not enough.
RUN apt-get -o APT::Update::Error-Mode=any -o Acquire::Retries=3 update \
    && apt-get upgrade --yes && rm -rf /var/lib/apt/lists/*

FROM base AS dependencies
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ENV PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv UV_HTTP_TIMEOUT=120 uv sync --locked --no-dev

FROM base AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ENV HF_HUB_DISABLE_TELEMETRY=1
ENV PATH="/app/.venv/bin:$PATH"
# Install tools belong to the build stage, not the serving image. The base's pip
# is also unnecessary: deployment replaces the image rather than installing code.
RUN python -m pip uninstall --yes pip
COPY --from=dependencies /app/.venv /app/.venv
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && python -m app.db.seed && python -m app.integrations.storage && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]
