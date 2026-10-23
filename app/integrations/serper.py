import asyncio
import http.client
import json
import ssl

from pydantic import SecretStr, ValidationError

from app.discovery import SearchResponse, SearchResult, SearchUsage


def _search(key: str, query: str):
    connection = http.client.HTTPSConnection(
        "google.serper.dev", timeout=10, context=ssl.create_default_context()
    )
    try:
        connection.request(
            "POST",
            "/search",
            body=json.dumps({"q": query, "hl": "en", "num": 10}),
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        if response.status != 200:
            return response.status, None
        body = response.read(1_000_001)
        if len(body) > 1_000_000:
            raise ValueError("oversized response")
        return response.status, json.loads(body)
    finally:
        connection.close()


class Serper:
    def __init__(self, api_key: SecretStr):
        self.api_key = api_key

    async def search(self, query: str) -> SearchResponse:
        key = self.api_key.get_secret_value().strip()
        if not key:
            return SearchResponse(usage=SearchUsage(requests=0, error="missing_api_key"))
        try:
            status, data = await asyncio.to_thread(_search, key, query)
            if status != 200:
                error = {401: "authentication", 403: "authentication", 429: "rate_limited"}.get(
                    status, "provider_http_error"
                )
                return SearchResponse(usage=SearchUsage(error=error))
            if (
                not isinstance(data, dict)
                or "error" in data
                or not isinstance(data.get("organic"), list)
            ):
                raise ValueError("invalid provider response")
            results = []
            for position, row in enumerate(data["organic"][:100], 1):
                if not isinstance(row, dict):
                    continue
                try:
                    results.append(
                        SearchResult(
                            url=row.get("link"),
                            title=row.get("title"),
                            snippet=row.get("snippet", ""),
                            position=position,
                            query=query,
                        )
                    )
                except (ValidationError, ValueError):
                    continue
            credits = data.get("credits")
            return SearchResponse(
                results=results,
                usage=SearchUsage(
                    returned_results=len(data["organic"]),
                    reported_credits=credits if type(credits) is int and credits >= 0 else None,
                ),
            )
        except (OSError, http.client.HTTPException):
            return SearchResponse(usage=SearchUsage(error="provider_unavailable"))
        except (ValueError, UnicodeError):
            return SearchResponse(usage=SearchUsage(error="invalid_response"))
