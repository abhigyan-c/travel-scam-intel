"""Bounded local read-load experiment. No providers, remote hosts or production data."""

import argparse
import asyncio
import hashlib
import json
import math
import platform
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from redis.asyncio import Redis
from sqlalchemy import event

from app.core.config import Settings
from app.main import create_app
from app.models import Claim, Document, Evidence, Hypothesis, Location, Source
from benchmarks.research import isolated_database


async def seed(sessions, patterns=50):
    async with sessions() as session, session.begin():
        city = Location(
            name="Synthetic load fixture", canonical_name="synthetic load fixture", type="city"
        )
        session.add(city)
        await session.flush()
        ids = []
        for index in range(patterns):
            hypothesis = Hypothesis(
                primary_location_id=city.id,
                title=f"Synthetic fare pattern {index}",
                canonical_description="Synthetic driver demanded extra money after an agreed fare.",
                category="transport",
                status="candidate",
            )
            session.add(hypothesis)
            await session.flush()
            ids.append(hypothesis.id)
            for report in range(5):
                quote = (
                    f"Synthetic example {index}-{report}: the driver demanded extra money "
                    "after an agreed fare."
                )
                digest = hashlib.sha256(quote.encode()).hexdigest()
                source = Source(
                    platform="example.org",
                    source_type="forum",
                    canonical_url=f"https://example.org/synthetic/{index}/{report}",
                    author_identifier=f"synthetic-{index}-{report}",
                )
                session.add(source)
                await session.flush()
                document = Document(
                    source_id=source.id,
                    raw_object_path="synthetic/not-stored",
                    content_hash=digest,
                    clean_text=quote,
                    clean_text_hash=digest,
                    language="en",
                    processing_status="processed",
                )
                session.add(document)
                await session.flush()
                claim = Claim(
                    document_id=document.id,
                    location_id=city.id,
                    claim_text=quote,
                    normalized_claim=quote.lower(),
                    claim_type="transport",
                    processing_status="processed",
                    structured_data={
                        "sensitive": False,
                        "entities": [],
                        "publication_status": "unreviewed",
                        "evidence_quote": quote,
                        "first_hand": True,
                        "location": None,
                        "occurred_at": None,
                        "extraction": {
                            "clean_text_hash": digest,
                            "quote_start": 0,
                            "quote_end": len(quote),
                        },
                    },
                )
                session.add(claim)
                await session.flush()
                session.add(
                    Evidence(
                        hypothesis_id=hypothesis.id,
                        claim_id=claim.id,
                        stance="supports",
                        semantic_match_score=1,
                        independence_score=1,
                        evidence_weight=1,
                    )
                )
        return city.id, ids


async def measure(sessions, settings, redis, metric_key):
    city, ids = await seed(sessions)
    application = create_app(settings)
    application.state.db_sessions = sessions
    application.state.redis = redis
    application.state.telemetry.key = lambda: metric_key
    paths = [
        f"/v1/locations/{city}/hypotheses?research=false&limit=10",
        f"/v1/hypotheses/{ids[0]}/evidence?limit=5",
        f"/v1/hypotheses/{ids[0]}",
    ]
    engine = sessions.kw["bind"]
    statements = 0

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def count(*args):
        nonlocal statements
        statements += 1

    reports = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://synthetic"
    ) as client:
        # Warm-up validates actual public eligibility, rather than timing empty pages.
        warm = await client.get(paths[0])
        assert warm.status_code == 200 and warm.json()["total"] == 50
        for concurrency in (1, 4, 8):
            latencies, statuses = [], {}
            gate = asyncio.Semaphore(concurrency)

            async def one(index, gate=gate, latencies=latencies, statuses=statuses):
                async with gate:
                    start = time.perf_counter()
                    response = await client.get(paths[index % len(paths)])
                    latencies.append((time.perf_counter() - start) * 1000)
                    statuses[str(response.status_code)] = (
                        statuses.get(str(response.status_code), 0) + 1
                    )

            before = statements
            start = time.perf_counter()
            await asyncio.gather(*(one(index) for index in range(60)))
            elapsed = time.perf_counter() - start
            ordered = sorted(latencies)
            reports.append(
                dict(
                    concurrency=concurrency,
                    requests=60,
                    seconds=elapsed,
                    requests_per_second=60 / elapsed,
                    statuses=statuses,
                    sql_statements=statements - before,
                    p50_ms=ordered[math.ceil(0.50 * len(ordered)) - 1],
                    p95_ms=ordered[math.ceil(0.95 * len(ordered)) - 1],
                    p99_ms=ordered[math.ceil(0.99 * len(ordered)) - 1],
                )
            )
    event.remove(engine.sync_engine, "before_cursor_execute", count)
    return dict(
        version="operational-load-v1",
        at=datetime.now(UTC).isoformat(),
        environment=dict(
            os=platform.system(),
            python=platform.python_version(),
            database_pool_size=5,
            http="in-process ASGI",
            telemetry_redis=True,
        ),
        corpus=dict(locations=1, patterns=50, documents=250, claims=250, evidence=250),
        measurements=reports,
        limits=(
            "Local warm reads only. Excludes TLS, network, embedding, "
            "worker/provider load and VPS capacity."
        ),
    )


async def main(output):
    # Use only the default loopback local database; credentials never enter the artifact.
    local = Settings(_env_file=None, environment="production", log_level="ERROR")
    redis = Redis.from_url(local.redis_url.get_secret_value())
    metric_key = "tsi:metrics:benchmark:" + uuid4().hex
    try:
        await redis.ping()
        async with isolated_database(local.database_url.get_secret_value()) as sessions:
            result = await measure(sessions, local, redis, metric_key)
    finally:
        await redis.delete(metric_key)
        await redis.aclose()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    if any(row["statuses"] != {"200": 60} for row in result["measurements"]):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".tools/pr37-load.json"))
    asyncio.run(main(parser.parse_args().output), loop_factory=asyncio.SelectorEventLoop)
