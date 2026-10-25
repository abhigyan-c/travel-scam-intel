"""Opt-in paid semantic checks with synthetic data; no network in ordinary test runs."""

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.db.hypothesis_resolution import resolve_hypothesis
from app.embeddings import Embedder, text_hash
from app.integrations.sarvam import Sarvam
from app.models import Claim, Document, Hypothesis, Location, Source
from app.models.location import LocationType
from app.models.source import SourceType

CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures/hypothesis_resolution_cases.json").read_text()
)
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        any(
            os.getenv(name) != "1"
            for name in ("RUN_INFRA_TESTS", "RUN_EMBEDDING_TESTS", "RUN_SARVAM_TESTS")
        ),
        reason="opt-in: local infrastructure, cached embedding model and live Sarvam credentials",
    ),
]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
async def test_real_sarvam_false_merge_regressions(session, case):
    embedder = Embedder(os.getenv("EMBEDDING_CACHE_DIR", ".tools/embedding-cache"))
    await embedder.preload()
    place = Location(name="Bangkok", canonical_name=str(uuid4()), type=LocationType.CITY)
    source = Source(platform="synthetic-resolver-validation", source_type=SourceType.USER_REPORT)
    session.add_all([place, source])
    await session.flush()
    document = Document(
        source_id=source.id, raw_object_path="raw/synthetic-resolver", content_hash="d" * 64
    )
    session.add(document)
    await session.flush()
    claim = Claim(
        document_id=document.id,
        location_id=place.id,
        claim_text=case["claim"],
        normalized_claim=case["claim"],
        claim_type="synthetic-fixture",
    )
    content = case["title"] + "\n" + case["description"]
    hypothesis = Hypothesis(
        primary_location_id=place.id,
        title=case["title"],
        canonical_description=case["description"],
        category="synthetic-fixture",
        embedding=(await embedder.embed([content]))[0],
        embedding_model=embedder.model,
        embedding_revision=embedder.revision,
        embedding_input_hash=text_hash(content),
    )
    session.add_all([claim, hypothesis])
    await session.flush()
    result = await resolve_hypothesis(
        session, claim.id, embedder=embedder, client=Sarvam(Settings().sarvam_api_key)
    )
    print(
        json.dumps(
            {
                "case": case["name"],
                "decision": result.decision,
                "review": result.needs_review,
                "error": result.error,
                "reason": result.details.get("decision"),
                "completion": result.details.get("completion"),
            }
        )
    )
    assert result.error is None
    assert result.decision in case["expected"]
    assert result.needs_review
