import json
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from app.discovery import SearchResponse, SearchResult, SearchUsage, discover, generate_queries
from app.integrations import serper
from app.integrations.serper import Serper


def response(url="https://example.org/report", position=1, error=None):
    return SearchResponse(
        results=[]
        if error
        else [
            SearchResult(
                url=url,
                title="Report",
                snippet="Reported observation",
                position=position,
                query="fixture",
            )
        ],
        usage=SearchUsage(error=error),
    )


def test_queries_are_deterministic_and_bounded():
    assert generate_queries("  Bangkok  ")[0] == "Bangkok tourist scams"
    assert generate_queries("Fiji")[-1] == "Fiji scams Reddit"
    assert len(generate_queries("Fiji")) == 6
    with pytest.raises(ValueError):
        generate_queries(" ")
    with pytest.raises(ValueError):
        generate_queries("a" * 201)


async def test_deduplication_ranking_filtering_and_partial_results():
    provider = AsyncMock()
    provider.search.side_effect = [
        SearchResponse(
            results=[
                SearchResult(url="https://EXAMPLE.org:443/report#fragment", position=5, query="q"),
                SearchResult(url="http://127.0.0.1/", position=1, query="q"),
                SearchResult(url="https://example.org/a.pdf", position=1, query="q"),
            ],
            usage=SearchUsage(),
        ),
        response(position=1),
        response(error="rate_limited"),
    ]
    result = await discover("Bangkok", provider)
    assert result.status == "partial" and len(result.results) == 1
    assert result.results[0].url == "https://example.org/report"
    assert result.results[0].position == 1
    assert len(result.usage) == 3 and sum(row.requests for row in result.usage) == 3


@pytest.mark.parametrize("error,status", [(None, "empty"), ("provider_http_error", "failed")])
async def test_empty_is_not_failure(error, status):
    provider = AsyncMock()
    provider.search.return_value = SearchResponse(usage=SearchUsage(error=error))
    result = await discover("Fiji", provider, max_queries=1)
    assert result.status == status and result.results == []


async def test_deadline_preserves_completed_results():
    import asyncio

    provider = AsyncMock()

    async def search(query):
        if "common" in query:
            await asyncio.sleep(10)
        return response()

    provider.search.side_effect = search
    result = await discover("Fiji", provider, timeout=0.03)
    assert result.status == "partial" and len(result.results) == 1
    assert result.usage[-1].error == "deadline"


@pytest.mark.parametrize(
    "status,error", [(401, "authentication"), (429, "rate_limited"), (503, "provider_http_error")]
)
async def test_provider_errors_are_sanitized(monkeypatch, status, error):
    monkeypatch.setattr(serper, "_search", lambda *args: (status, {"message": "secret"}))
    result = await Serper(SecretStr("secret")).search("Fiji tourist scams")
    assert result.usage.error == error and result.usage.requests == 1
    assert "secret" not in result.model_dump_json()


@pytest.mark.parametrize(
    "data", [None, [], {}, {"organic": {}}, {"error": "secret", "organic": []}]
)
async def test_malformed_payload_rejected(monkeypatch, data):
    monkeypatch.setattr(serper, "_search", lambda *args: (200, data))
    result = await Serper(SecretStr("key")).search("q")
    assert result.usage.error == "invalid_response"


async def test_normalization_and_reported_usage(monkeypatch):
    monkeypatch.setattr(
        serper,
        "_search",
        lambda *args: (
            200,
            {
                "organic": [
                    {
                        "link": "https://EXAMPLE.org:443/report#top",
                        "title": "Fixture",
                        "snippet": "Text",
                    },
                    {"link": "file:///private"},
                    None,
                ],
                "credits": 1,
            },
        ),
    )
    result = await Serper(SecretStr("key")).search("q")
    assert result.usage.returned_results == 3 and result.usage.reported_credits == 1
    assert len(result.results) == 1 and result.results[0].url == "https://example.org/report"


async def test_missing_key_does_not_call_provider(monkeypatch):
    def forbidden(*args):
        raise AssertionError("should not request")

    monkeypatch.setattr(serper, "_search", forbidden)
    result = await Serper(SecretStr("")).search("q")
    assert result.usage.requests == 0 and result.usage.error == "missing_api_key"


def test_wire_contract_keeps_key_in_header_and_requests_english(monkeypatch):
    calls = []

    class Connection:
        status = 200

        def __init__(self, host, **kwargs):
            assert host == "google.serper.dev"

        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))

        def getresponse(self):
            return self

        def read(self, limit):
            return b'{"organic": [], "credits": 1}'

        def close(self):
            pass

    monkeypatch.setattr(serper.http.client, "HTTPSConnection", Connection)
    assert serper._search("secret", "Fiji tourist scams")[0] == 200
    method, path, kwargs = calls[0]
    assert method == "POST" and path == "/search"
    assert kwargs["headers"]["X-API-KEY"] == "secret"
    assert json.loads(kwargs["body"]) == {"q": "Fiji tourist scams", "hl": "en", "num": 10}
