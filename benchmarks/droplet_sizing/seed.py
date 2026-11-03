"""Synthetic public corpus with valid provenance; never targets a real database.

Uses deterministic placeholder vectors for stored data size. Runtime semantic
matching and report enrichment still run the real pinned embedding model.
"""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import insert, text

from app.core.config import Settings
from app.db.session import create_database_engine
from app.embeddings import MODEL, REVISION, text_hash
from app.models import Claim, Document, Evidence, Hypothesis, Location, Source


def identity(kind, index):
    return uuid5(NAMESPACE_URL, f"sizing-only:{kind}:{index}")


async def seed(start, stop):
    settings = Settings()
    if os.environ.get("BENCHMARK_SIZING") != "1" or (
        "travel:sizing-only@postgres:5432/travel_scam_intel"
        not in settings.database_url.get_secret_value()
    ):
        raise ValueError("isolated_compose_database_required")
    engine = create_database_engine(settings)
    now = datetime.now(UTC)
    vector = [1.0] + [0.0] * 383
    async with engine.begin() as connection:
        if start == 0:
            await connection.execute(
                insert(Location),
                [
                    dict(
                        id=identity("location", i),
                        name=f"Sizing City {i}",
                        canonical_name=f"sizing city {i}",
                        type="city",
                        country_code="FJ",
                    )
                    for i in range(55)
                ],
            )
        for offset in range(start, stop, 100):
            hypotheses, sources, documents, claims, evidence = [], [], [], [], []
            for index in range(offset, min(offset + 100, stop)):
                location = identity("location", index % 50)
                summary = (
                    f"A driver demanded extra money after an agreed fixed fare, pattern {index}."
                )
                hypotheses.append(
                    dict(
                        id=identity("hypothesis", index),
                        primary_location_id=location,
                        title=summary,
                        canonical_description=summary,
                        category="transport",
                        status="active",
                        confidence_score=0.7,
                        prevalence_score=0.5,
                        recency_score=0.8,
                        embedding=vector,
                        embedding_model=MODEL,
                        embedding_revision=REVISION,
                        embedding_input_hash=text_hash(summary),
                    )
                )
                for observation in range(5):
                    item = index * 5 + observation
                    quote = (
                        f"I agreed a fixed fare but the driver later demanded extra money. "
                        f"This is a synthetic sizing observation {item}, never a real allegation."
                    )
                    body = (
                        quote + "\n" + ("Travelers should confirm the fare before starting. " * 20)
                    )
                    digest = text_hash(body)
                    sources.append(
                        dict(
                            id=identity("source", item),
                            platform="example.org",
                            canonical_url=f"https://example.org/sizing/{item}",
                            source_type="forum",
                            author_identifier=f"synthetic-{item}",
                        )
                    )
                    documents.append(
                        dict(
                            id=identity("document", item),
                            source_id=identity("source", item),
                            raw_object_path=f"synthetic/{item}",
                            content_hash=text_hash(str(item)),
                            clean_text=body,
                            clean_text_hash=digest,
                            language="en",
                            processing_status="processed",
                            published_at=now,
                        )
                    )
                    claims.append(
                        dict(
                            id=identity("claim", item),
                            document_id=identity("document", item),
                            location_id=location,
                            claim_text=summary,
                            normalized_claim=summary.lower(),
                            claim_type="transport",
                            occurred_at=now - timedelta(days=observation),
                            extraction_confidence=0.9,
                            processing_status="processed",
                            embedding=vector,
                            embedding_model=MODEL,
                            embedding_revision=REVISION,
                            embedding_input_hash=text_hash(summary),
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
                    )
                    evidence.append(
                        dict(
                            id=identity("evidence", item),
                            hypothesis_id=identity("hypothesis", index),
                            claim_id=identity("claim", item),
                            stance="supports",
                            semantic_match_score=1,
                            independence_score=1,
                            evidence_weight=1,
                        )
                    )
            for model, rows in [
                (Hypothesis, hypotheses),
                (Source, sources),
                (Document, documents),
                (Claim, claims),
                (Evidence, evidence),
            ]:
                await connection.execute(insert(model), rows)
        await connection.execute(text("ANALYZE"))
        size = await connection.scalar(text("SELECT pg_database_size(current_database())"))
    await engine.dispose()
    print(
        json.dumps(
            {
                "patterns": stop,
                "claims": stop * 5,
                "database_bytes": size,
                "city": str(identity("location", 0)),
                "report_city": str(identity("location", 50)),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--stop", type=int, default=1000)
    args = parser.parse_args()
    asyncio.run(seed(args.start, args.stop))
