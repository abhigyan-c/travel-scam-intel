import asyncio
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.core.config import Settings
from app.discovery import SearchResponse, SearchResult, SearchUsage
from app.extraction import ClaimBatch, ExtractedClaim
from app.integrations.fetch import FetchedPage, FetchError
from app.integrations.sarvam import Completion
from app.integrations.storage import create_storage_client, ensure_bucket
from app.models import Claim, Document, Source
from app.relevance import Relevance
from app.research import ResearchLimits, research_location

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]
CASE = json.loads((Path(__file__).parents[1] / "fixtures/extraction_cases.json").read_text())[0]


@pytest.fixture
def boundaries():
    settings = Settings()
    storage = create_storage_client(settings)
    ensure_bucket(storage, settings.s3_bucket)
    keys = set()
    url = f"https://example.org/research/{uuid4()}"
    provider, fetcher, llm = AsyncMock(), AsyncMock(), AsyncMock()
    provider.search.return_value = SearchResponse(
        results=[
            SearchResult(url=url, title="Fixture report", position=1, query="Bangkok tourist scams")
        ],
        usage=SearchUsage(returned_results=1, reported_credits=1),
    )

    async def fetch(requested):
        raw = CASE["text"].encode()
        keys.add(
            f"raw/{hashlib.sha256(requested.encode()).hexdigest()}/{hashlib.sha256(raw).hexdigest()}"
        )
        return FetchedPage(requested, raw, "text/plain")

    fetcher.fetch.side_effect = fetch

    async def complete(**kwargs):
        data = (
            Relevance(relevant=True, category="fare", confidence=0.9, reason="Reported fare demand")
            if kwargs["schema"] is Relevance
            else ClaimBatch(claims=[ExtractedClaim.model_validate(CASE["claim"])])
        )
        return Completion(data=data, requests=1, usage={"total_tokens": 100})

    llm.complete.side_effect = complete
    yield (
        {
            "provider": provider,
            "fetcher": fetcher,
            "llm": llm,
            "storage": storage,
            "bucket": settings.s3_bucket,
        },
        url,
    )
    try:
        for key in keys:
            storage.delete_object(Bucket=settings.s3_bucket, Key=key)
    finally:
        storage.close()


def limits(**changes):
    return ResearchLimits(queries=1, **changes)


async def test_location_alone_produces_traceable_claim_and_retry_reuses_llm_outputs(
    session, boundaries
):
    deps, url = boundaries
    result = await research_location(session, "Bangkok", **deps, limits=limits())
    assert result.status == "ok" and result.complete and len(result.claim_ids) == 1
    stored = await session.scalar(
        select(Claim)
        .where(Claim.id == UUID(result.claim_ids[0]))
        .options(joinedload(Claim.document).joinedload(Document.source))
    )
    assert stored.document.source.canonical_url == url
    assert stored.document.source.platform == "example.org"
    assert str(stored.location_id) == result.location_id
    assert stored.structured_data["publication_status"] == "held"
    lineage = stored.structured_data["extraction"]
    assert (
        stored.document.clean_text[lineage["quote_start"] : lineage["quote_end"]]
        == CASE["claim"]["evidence_quote"]
    )
    repeated = await research_location(session, "Bangkok", **deps, limits=limits())
    assert repeated.claim_ids == result.claim_ids and repeated.usage["llm_calls"] == 0
    assert deps["llm"].complete.await_count == 2
    assert result.usage["search"][0]["requests"] == 1


async def test_fetch_failure_does_not_discard_later_success(session, boundaries):
    deps, url = boundaries
    failed = f"https://example.org/failure/{uuid4()}"
    deps["provider"].search.return_value.results.insert(
        0, SearchResult(url=failed, position=1, query="q")
    )
    original = deps["fetcher"].fetch.side_effect

    async def fetch(requested):
        if requested == failed:
            raise FetchError("http_status")
        return await original(requested)

    deps["fetcher"].fetch.side_effect = fetch
    result = await research_location(session, "Bangkok", **deps, limits=limits())
    assert result.status == "partial" and len(result.claim_ids) == 1
    assert result.fetched == 2 and result.errors[0]["stage"] == "ingestion"
    assert await session.scalar(select(Source.id).where(Source.canonical_url == failed)) is None


@pytest.mark.parametrize("error,status", [(None, "empty"), ("rate_limited", "failed")])
async def test_successful_empty_is_distinct_from_provider_failure(
    session, boundaries, error, status
):
    deps, _ = boundaries
    deps["provider"].search.return_value = SearchResponse(usage=SearchUsage(error=error))
    result = await research_location(session, "Fiji", **deps, limits=limits())
    assert result.status == status and result.claim_ids == []
    assert (result.message == "Nothing was found.") == (error is None)
    deps["fetcher"].fetch.assert_not_called()
    deps["llm"].complete.assert_not_called()


async def test_fetch_and_model_call_limits_are_enforced(session, boundaries):
    deps, url = boundaries
    deps["provider"].search.return_value.results.append(
        SearchResult(url=url + "/second", position=2, query="q")
    )
    result = await research_location(
        session, "Bangkok", **deps, limits=limits(fetches=1, llm_calls=1)
    )
    assert result.status == "partial" and result.fetched == 1
    assert result.claim_ids == [] and result.usage["llm_calls"] == 1
    deps["fetcher"].fetch.assert_awaited_once()
    deps["llm"].complete.assert_awaited_once()


async def test_deadline_retains_previous_claims(session, boundaries):
    deps, url = boundaries
    slow = url + "/slow"
    deps["provider"].search.return_value.results.append(
        SearchResult(url=slow, position=2, query="q")
    )
    original = deps["fetcher"].fetch.side_effect

    async def fetch(requested):
        if requested == slow:
            await asyncio.sleep(5)
        return await original(requested)

    deps["fetcher"].fetch.side_effect = fetch
    result = await research_location(session, "Bangkok", **deps, limits=limits(timeout=1))
    assert result.status == "partial" and len(result.claim_ids) == 1
    assert any(error["code"] == "deadline" for error in result.errors)


async def test_model_failure_is_incomplete_not_negative(session, boundaries):
    deps, _ = boundaries
    deps["llm"].complete.side_effect = None
    deps["llm"].complete.return_value = Completion(error="rate_limited", requests=1)
    result = await research_location(session, "Bangkok", **deps, limits=limits())
    assert result.status == "partial" and result.claim_ids == []
    assert len(result.documents) == 1 and not result.documents[0]["relevance_complete"]
    assert result.usage["llm"][0]["error"] == "rate_limited"


async def test_long_article_reserves_budget_for_extraction(session, boundaries, monkeypatch):
    deps, _ = boundaries
    monkeypatch.setitem(CASE, "text", CASE["text"] + " Additional source account. " * 400)
    result = await research_location(session, "Bangkok", **deps, limits=limits(llm_calls=2))
    assert result.status == "partial" and len(result.claim_ids) == 1
    assert result.usage["llm_calls"] == 2 and result.documents[0]["relevance_passages"] == 1
