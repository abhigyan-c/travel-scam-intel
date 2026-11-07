import asyncio
from unittest.mock import AsyncMock

import pytest

from app.acquisition import AcquisitionPolicy, CandidateFetcher, dedup_key, diversify
from app.discovery import SearchResponse, SearchResult, SearchUsage, discover
from app.integrations import fetch
from app.integrations.fetch import Fetcher, FetchError


def result(host, path, position=1):
    return SearchResult(url=f"https://{host}/{path}", position=position, query="fixture")


def test_diversity_preserves_rank_inside_hosts_without_discarding_sources():
    a, b, c, d = (
        result("a.example", "1"),
        result("a.example", "2"),
        result("b.example", "1"),
        result("a.example", "3"),
    )
    assert diversify([a, b, c, d]) == [a, c, b, d]


def test_tracking_dedup_preserves_resource_parameters_and_original_urls():
    assert dedup_key("https://a.example/p?id=1&utm_source=a") == dedup_key(
        "https://a.example/p?id=1&utm_source=b"
    )
    assert dedup_key("https://a.example/p?id=1") != dedup_key("https://a.example/p?id=2")


async def test_discovery_keeps_the_best_original_tracking_url_without_rewriting_it():
    provider = AsyncMock()
    source = "https://a.example/p?id=1&utm_source=best"
    provider.search.return_value = SearchResponse(
        results=[
            SearchResult(url="https://a.example/p?id=1&utm_source=other", position=2, query="q"),
            SearchResult(url=source, position=1, query="q"),
            SearchResult(url="https://a.example/p?id=2", position=3, query="q"),
        ],
        usage=SearchUsage(returned_results=3),
    )
    found = await discover("Bangkok", provider, max_queries=1)
    assert [r.url for r in found.results] == [source, "https://a.example/p?id=2"]


async def test_diversity_applies_before_the_candidate_limit_and_reports_truncation():
    provider = AsyncMock()
    a, b, c = result("a.example", "1"), result("a.example", "2"), result("b.example", "1")
    provider.search.return_value = SearchResponse(results=[a, b, c], usage=SearchUsage())
    found = await discover("Bangkok", provider, max_queries=1, max_results=2)
    assert found.results == [a, c] and found.truncated


async def test_all_filtered_sources_are_incomplete_not_successfully_empty():
    provider = AsyncMock()
    provider.search.return_value = SearchResponse(
        results=[result("a.example", "file.pdf")], usage=SearchUsage(returned_results=1)
    )
    found = await discover("Bangkok", provider, max_queries=1)
    assert found.status == "partial" and found.filtered == 1 and not found.results


async def test_provider_returned_but_unparsable_sources_are_not_a_true_empty_search():
    provider = AsyncMock()
    provider.search.return_value = SearchResponse(results=[], usage=SearchUsage(returned_results=2))
    found = await discover("Bangkok", provider, max_queries=1)
    assert found.status == "partial" and found.filtered == 2


async def test_parallel_search_is_bounded_and_keeps_deterministic_usage_order():
    active = maximum = 0

    async def search(query):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.02)
        active -= 1
        return SearchResponse(results=[], usage=SearchUsage())

    provider = AsyncMock()
    provider.search.side_effect = search
    found = await discover("Bangkok", provider, max_queries=6, concurrency=2)
    assert maximum == 2 and provider.search.await_count == 6 and len(found.usage) == 6


async def test_unstarted_searches_do_not_claim_network_requests_on_deadline():
    async def slow(query):
        await asyncio.sleep(1)

    provider = AsyncMock()
    provider.search.side_effect = slow
    found = await discover("Bangkok", provider, max_queries=6, timeout=0.01, concurrency=2)
    assert provider.search.await_count == 2
    assert sum(u.requests for u in found.usage) == 2
    assert all(u.error == "deadline" for u in found.usage)


def test_access_blocks_are_run_local_but_page_404s_are_not_host_blocks():
    policy = AcquisitionPolicy()
    policy.failure("https://www.a.example/1", FetchError("http_status", status=403))
    assert policy.allow("https://a.example/2")
    policy.failure("https://a.example/2", FetchError("http_status", status=403))
    assert not policy.allow("https://a.example/2")
    assert AcquisitionPolicy().allow("https://a.example/2")
    other = AcquisitionPolicy()
    for _ in range(3):
        other.failure("https://a.example/missing", FetchError("http_status", status=404))
    assert other.allow("https://a.example/real")


def test_redirect_denial_does_not_blacklist_the_origins_other_pages():
    policy = AcquisitionPolicy()
    policy.failure(
        "https://a.example/redirect", FetchError("http_status", status=403, host="b.example")
    )
    policy.failure(
        "https://a.example/redirect2", FetchError("http_status", status=403, host="b.example")
    )
    assert policy.allow("https://a.example/other") and not policy.allow("https://b.example/other")


def test_repeated_empty_and_transport_failures_defer_only_in_current_run():
    policy = AcquisitionPolicy()
    for _ in range(2):
        policy.processed("https://a.example/", "empty")
    assert not policy.allow("https://a.example/next")
    for _ in range(2):
        policy.failure("https://b.example/", FetchError("fetch_timeout"))
    assert not policy.allow("https://b.example/next")
    assert AcquisitionPolicy().allow("https://b.example/next")


async def test_fetch_cap_cancels_slow_fetch_with_a_safe_specific_error():
    delegate = AsyncMock()

    async def slow(url):
        await asyncio.sleep(1)

    delegate.fetch.side_effect = slow
    with pytest.raises(FetchError, match="acquisition_timeout"):
        await CandidateFetcher(delegate, 0.01).fetch("https://a.example/")


async def test_native_rate_limit_is_not_immediately_retried(monkeypatch):
    monkeypatch.setattr(fetch, "resolve_public", AsyncMock(return_value="93.184.216.34"))
    calls = []

    def limited(*args):
        calls.append(args)
        return 429, {"retry-after": "60"}, b""

    monkeypatch.setattr(fetch, "_request", limited)
    with pytest.raises(FetchError) as caught:
        await Fetcher(host_interval=0).fetch("https://a.example/")
    assert str(caught.value) == "rate_limited" and caught.value.status == 429
    assert caught.value.host == "a.example" and len(calls) == 1


async def test_redirect_to_a_run_deferred_host_stops_before_its_dns_or_http(monkeypatch):
    policy = AcquisitionPolicy()
    policy.failure("https://b.example/", FetchError("rate_limited", status=429))
    resolver = AsyncMock(return_value="93.184.216.34")
    monkeypatch.setattr(fetch, "resolve_public", resolver)
    calls = []

    def redirect(*args):
        calls.append(args)
        return 302, {"location": "https://b.example/other"}, b""

    monkeypatch.setattr(fetch, "_request", redirect)
    with pytest.raises(FetchError, match="host_deferred"):
        await CandidateFetcher(Fetcher(host_interval=0), 1, policy).fetch("https://a.example/")
    assert len(calls) == 1 and resolver.await_count == 1


async def test_search_lease_loss_cancels_siblings_without_wrapping_the_exception():
    from app.research import ResearchLeaseLost

    active = 0

    async def search(query):
        nonlocal active
        active += 1
        try:
            if "tourist scams" in query:
                await asyncio.sleep(0.01)
                raise ResearchLeaseLost()
            await asyncio.sleep(1)
        finally:
            active -= 1

    provider = AsyncMock()
    provider.search.side_effect = search
    with pytest.raises(ResearchLeaseLost):
        await discover("Bangkok", provider, max_queries=3)
    assert active == 0


def test_public_ipv6_access_feedback_uses_the_actual_host_identity():
    host = "2606:4700:4700::1111"
    policy = AcquisitionPolicy()
    for _ in range(2):
        policy.failure(f"https://[{host}]/", FetchError("http_status", status=403, host=host))
    assert not policy.allow(f"https://[{host}]/other")


def test_controlled_benchmark_exposes_gains_and_late_recovery_loss():
    import json

    from benchmarks.acquisition import DATA, simulate

    corpus = json.loads((DATA / "acquisition-cases.json").read_text())
    cases = {c["id"]: c for c in corpus["cases"]}
    assert simulate(cases["repeated-denials"], revised=True)["unique_readable_documents"] == 5
    assert simulate(cases["repeated-denials"], revised=False)["unique_readable_documents"] == 3
    assert simulate(cases["late-host-recovery"], revised=True)["unique_readable_documents"] == 0
    assert simulate(cases["late-host-recovery"], revised=False)["unique_readable_documents"] == 1
