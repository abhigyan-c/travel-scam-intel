import os
import time
from uuid import uuid4

import pytest

from app.db.embeddings import backfill_embeddings, search_candidates
from app.embeddings import Embedder
from app.models import Hypothesis, Location
from app.models.location import LocationType

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INFRA_TESTS") != "1" or os.getenv("RUN_EMBEDDING_TESTS") != "1",
        reason="set RUN_INFRA_TESTS=1 and RUN_EMBEDDING_TESTS=1; preload pinned model first",
    ),
]

CASES = [
    (
        "Taxi meter overcharging",
        "A taxi driver refuses to use the meter and demands an inflated fare.",
        "The cab driver would not turn on the meter and wanted far too much money.",
    ),
    (
        "Closed temple tuk-tuk diversion",
        "A stranger falsely says the temple is closed and directs tourists "
        "to a cheap tuk-tuk shopping tour.",
        "Someone outside the palace said it was shut today and offered a bargain ride to shops.",
    ),
    (
        "ATM card skimming",
        "A device on an ATM copies payment card data and a hidden camera records the PIN.",
        "My bank card details were copied by a reader attached to the cash machine.",
    ),
    (
        "Gem investment fraud",
        "A jewelry seller offers worthless gemstones as valuable investments "
        "and promises high resale profits.",
        "The shop sold me cheap stones claiming I could sell them back home for a huge profit.",
    ),
]


async def test_real_cpu_mechanism_retrieval_and_repeatable_backfill(session):
    embedder = Embedder(os.getenv("EMBEDDING_CACHE_DIR", ".tools/embedding-cache"))
    started = time.perf_counter()
    await embedder.preload()
    preload = time.perf_counter() - started
    place = Location(name="Retrieval fixture", canonical_name=str(uuid4()), type=LocationType.CITY)
    session.add(place)
    await session.flush()
    rows = [
        Hypothesis(
            primary_location_id=place.id,
            title=title,
            canonical_description=description,
            category="fixture",
        )
        for title, description, _ in CASES
    ]
    session.add_all(rows)
    await session.flush()
    result = await backfill_embeddings(session, Hypothesis, embedder)
    assert not result["errors"] and result["updated"] >= len(rows)
    assert (await backfill_embeddings(session, Hypothesis, embedder))["updated"] == 0
    elapsed = []
    for row, (_, _, query) in zip(rows, CASES, strict=True):
        started = time.perf_counter()
        candidates = await search_candidates(session, query, place.id, embedder, limit=4)
        elapsed.append(time.perf_counter() - started)
        assert candidates[0].hypothesis_id == row.id
    print(
        {
            "cached_preload_seconds": round(preload, 3),
            "retrieval_seconds": [round(value, 3) for value in elapsed],
            "top1": "4/4",
        }
    )
