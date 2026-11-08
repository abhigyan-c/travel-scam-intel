import argparse
import asyncio
import ipaddress
import unicodedata
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from app.acquisition import dedup_key, diversify
from app.db.ingestion import DiscoveryResult

DISCOVERY_VERSION = "discovery-v2"


class SearchResult(DiscoveryResult):
    snippet: str = ""
    position: int = Field(ge=1)
    query: str


class SearchUsage(BaseModel):
    requests: int = 1
    returned_results: int = 0
    reported_credits: int | None = None
    error: str | None = None


class SearchResponse(BaseModel):
    results: list[SearchResult] = Field(default_factory=list)
    usage: SearchUsage


class SearchProvider(Protocol):
    async def search(self, query: str) -> SearchResponse: ...


class DiscoveryResponse(BaseModel):
    results: list[SearchResult]
    usage: list[SearchUsage]
    status: str
    filtered: int = 0
    truncated: bool = False


def generate_queries(location: str, *, topic: str | None = None) -> list[str]:
    location = " ".join(unicodedata.normalize("NFKC", location).split())
    if not location or len(location) > 200 or any(ord(c) < 32 for c in location):
        raise ValueError("location must contain 1–200 printable characters")
    if topic is not None:
        topic = " ".join(unicodedata.normalize("NFKC", topic).split())
        if not topic or len(topic) > 120 or any(ord(c) < 32 for c in topic):
            raise ValueError("topic must contain 1–120 printable characters")
        return [
            f"{location} {topic} {suffix}"
            for suffix in (
                "traveler reports",
                "tourist warnings",
                "scams",
                "travel experiences",
                "fraud",
                "Reddit",
            )
        ]
    return [
        f"{location} {suffix}"
        for suffix in (
            "tourist scams",
            "common scams travel blog",
            "tourist traps travel blog",
            "taxi scams",
            "tourist fraud",
            "scams Reddit",
        )
    ]


def usable_url(url: str) -> bool:
    host = urlsplit(url).hostname
    if (
        host == "localhost"
        or host.endswith((".localhost", ".local", ".internal"))
        or "." not in host
    ):
        return False
    try:
        address = ipaddress.ip_address(host)
        if not address.is_global or address.is_multicast:
            return False
    except ValueError:
        pass
    return not urlsplit(url).path.lower().endswith((".pdf", ".jpg", ".png", ".zip", ".mp4"))


async def discover(
    location: str,
    provider: SearchProvider,
    *,
    max_queries: int = 3,
    max_results: int = 30,
    timeout: float = 25,
    topic: str | None = None,
    concurrency: int = 2,
    query_plan: list[str] | None = None,
) -> DiscoveryResponse:
    if not 1 <= max_queries <= 6 or not 1 <= max_results <= 100 or timeout <= 0:
        raise ValueError("invalid discovery limits")
    if not 1 <= concurrency <= 3:
        raise ValueError("invalid discovery concurrency")
    queries = (generate_queries(location, topic=topic) if query_plan is None else query_plan)[
        :max_queries
    ]
    if any(not q.strip() or len(q) > 400 or any(ord(c) < 32 for c in q) for q in queries):
        raise ValueError("invalid query plan")
    found: dict[str, tuple[int, SearchResult]] = {}
    usage, filtered = [], 0
    deadline = asyncio.get_running_loop().time() + timeout
    semaphore = asyncio.Semaphore(concurrency)

    async def search(query):
        started = False
        try:
            async with asyncio.timeout_at(deadline), semaphore:
                started = True
                return await provider.search(query)
        except TimeoutError:
            return SearchResponse(usage=SearchUsage(requests=int(started), error="deadline"))

    tasks = [asyncio.create_task(search(query)) for query in queries]
    try:
        responses = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    for index, response in enumerate(responses):
        usage.append(response.usage)
        filtered += max(0, response.usage.returned_results - len(response.results))
        for result in response.results:
            if not usable_url(result.url):
                filtered += 1
                continue
            key = dedup_key(result.url)
            prior = found.get(key)
            if prior is None or (result.position, index) < (prior[1].position, prior[0]):
                found[key] = (index, result)
    ranked = [
        result
        for _, result in sorted(
            found.values(), key=lambda row: (row[1].position, row[0], row[1].url)
        )
    ]
    results = diversify(ranked)[:max_results]
    complete = len(usage) == len(queries) and all(item.error is None for item in usage)
    status = ("ok" if results else "empty") if complete else ("partial" if results else "failed")
    if not results and filtered and complete:
        status = "partial"
    return DiscoveryResponse(
        results=results,
        usage=usage,
        status=status,
        filtered=filtered,
        truncated=len(ranked) > max_results,
    )


async def _main(location: str, queries: int):
    from app.core.config import Settings
    from app.integrations.serper import Serper

    response = await discover(location, Serper(Settings().serper_api_key), max_queries=queries)
    print(response.model_dump_json(indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discover English reports using Serper")
    parser.add_argument("location")
    parser.add_argument("--queries", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(_main(args.location, args.queries))
