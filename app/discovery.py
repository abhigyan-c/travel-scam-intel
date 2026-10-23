import argparse
import asyncio
import ipaddress
import unicodedata
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from app.db.ingestion import DiscoveryResult


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


def generate_queries(location: str) -> list[str]:
    location = " ".join(unicodedata.normalize("NFKC", location).split())
    if not location or len(location) > 200 or any(ord(c) < 32 for c in location):
        raise ValueError("location must contain 1–200 printable characters")
    return [
        f"{location} {suffix}"
        for suffix in (
            "tourist scams",
            "common scams",
            "tourist traps",
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
) -> DiscoveryResponse:
    if not 1 <= max_queries <= 6 or not 1 <= max_results <= 100 or timeout <= 0:
        raise ValueError("invalid discovery limits")
    queries = generate_queries(location)[:max_queries]
    found: dict[str, tuple[int, SearchResult]] = {}
    usage = []
    deadline = asyncio.get_running_loop().time() + timeout
    for index, query in enumerate(queries):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        try:
            async with asyncio.timeout(remaining):
                response = await provider.search(query)
        except TimeoutError:
            usage.append(SearchUsage(error="deadline"))
            break
        usage.append(response.usage)
        for result in response.results:
            if not usable_url(result.url):
                continue
            prior = found.get(result.url)
            if prior is None or (result.position, index) < (prior[1].position, prior[0]):
                found[result.url] = (index, result)
    results = [
        result
        for _, result in sorted(
            found.values(), key=lambda row: (row[1].position, row[0], row[1].url)
        )
    ][:max_results]
    complete = len(usage) == len(queries) and all(item.error is None for item in usage)
    status = ("ok" if results else "empty") if complete else ("partial" if results else "failed")
    return DiscoveryResponse(results=results, usage=usage, status=status)


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
