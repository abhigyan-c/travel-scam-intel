import asyncio
from unittest.mock import AsyncMock

import pytest

from app.discovery import SearchResponse, SearchResult, SearchUsage
from app.research_progress import Cursor, Progress, SavedSearch


def cursor(**kwargs):
    return Cursor(
        caps=dict(queries=3, fetches=3, documents=2, llm_calls=4), seconds_cap=30, **kwargs
    )


async def test_reservations_are_atomic_durable_and_do_not_reset_on_restart():
    saved = []
    value = cursor()

    async def persist(session):
        saved.append(value.model_dump(mode="json"))

    progress = Progress(value, persist)
    assert sum(await asyncio.gather(*(progress.charge("llm_calls") for _ in range(10)))) == 4
    restarted = Progress(Cursor.model_validate(saved[-1]), AsyncMock())
    assert not await restarted.charge("llm_calls")
    assert await restarted.reserve_time(25) == 25
    assert await restarted.reserve_time(25) == 5
    assert await restarted.reserve_time(25) == 0


async def test_completed_query_is_reused_and_crashed_query_is_conservatively_charged():
    value = cursor()
    snapshots = []

    async def persist(session):
        snapshots.append(value.model_dump(mode="json"))

    progress = Progress(value, persist)
    delegate = AsyncMock()
    delegate.search.return_value = SearchResponse(
        results=[SearchResult(url="https://example.org/story", position=1, query="q")],
        usage=SearchUsage(returned_results=1),
    )
    search = SavedSearch(delegate, progress)
    await search.search("q")
    cached = await search.search("q")
    assert len(cached.results) == 1 and cached.usage.requests == 0
    assert delegate.search.await_count == 1
    delegate.search.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await search.search("interrupted")
    restarted = Cursor.model_validate(snapshots[-1])
    assert restarted.used["queries"] == 2
    assert restarted.queries["interrupted"]["attempts"] == 1
    assert "snippet" not in next(iter(restarted.candidates.values()))["source"]


def test_version_change_invalidates_derived_markers_but_preserves_source_and_budget():
    value = cursor(used={"llm_calls": 3}, versions={"old": "v0"})
    value.candidates["key"] = dict(
        document_id="immutable", done=True, applied={"claim": {}}, summary={}, attempts=1
    )
    progress = Progress(value, AsyncMock())
    progress.reconcile({"location": "Fiji"}, ["q"])
    assert value.used == {"llm_calls": 3}
    assert value.candidates["key"] == {"document_id": "immutable", "attempts": 1}
    with pytest.raises(ValueError, match="target_changed"):
        progress.reconcile({"location": "Rome"}, ["q"])


def test_unknown_cursor_schema_fails_closed():
    with pytest.raises(ValueError, match="unsupported_continuation"):
        cursor(schema_version="future")


async def test_unusable_search_results_remain_distinct_from_successful_empty():
    progress = Progress(cursor(), AsyncMock())
    provider = AsyncMock()
    provider.search.return_value = SearchResponse(
        results=[SearchResult(url="https://example.org/download.pdf", position=1, query="q")],
        usage=SearchUsage(returned_results=1),
    )
    await SavedSearch(provider, progress).search("q")
    assert not progress.pending()
    assert progress.cursor.queries["q"]["unusable"]


async def test_failed_fence_prevents_external_call():
    from app.research import ResearchLeaseLost

    progress = Progress(cursor(), AsyncMock(side_effect=ResearchLeaseLost))
    provider = AsyncMock()
    with pytest.raises(ResearchLeaseLost):
        await SavedSearch(provider, progress).search("q")
    provider.search.assert_not_called()
