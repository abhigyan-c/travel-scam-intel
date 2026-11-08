"""Versioned bounded cursors; storage/fencing is supplied by the job owner."""

import asyncio
import hashlib
import json
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.acquisition import AcquisitionPolicy, dedup_key, diversify
from app.discovery import SearchResponse, SearchResult, SearchUsage, usable_url
from app.integrations.fetch import FetchError


def versions():
    from app.acquisition import POLICY_VERSION
    from app.db.hypothesis_lifecycle import LIFECYCLE_VERSION
    from app.db.resolution import RESOLUTION_VERSION
    from app.discovery import DISCOVERY_VERSION
    from app.embeddings import MODEL as embedding
    from app.embeddings import REVISION
    from app.extraction import PROMPT_VERSION as extraction
    from app.hypothesis_resolution import VERSION as hypothesis
    from app.integrations.sarvam import MODEL
    from app.processing import PROCESSING_VERSION
    from app.relevance import PROMPT_VERSION as relevance

    return dict(
        discovery=DISCOVERY_VERSION,
        acquisition=POLICY_VERSION,
        processing=PROCESSING_VERSION,
        relevance=relevance,
        extraction=extraction,
        location=RESOLUTION_VERSION,
        hypothesis=hypothesis,
        lifecycle=LIFECYCLE_VERSION,
        llm=MODEL,
        embedding=embedding,
        embedding_revision=REVISION,
    )


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def candidate_key(url):
    return digest(dedup_key(url))


class Cursor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "continuation-v1"
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    goal: str | None = None
    versions: dict[str, str] = Field(default_factory=versions)
    caps: dict[str, int]
    used: dict[str, int] = Field(default_factory=dict)
    seconds_cap: float = Field(gt=0, le=270)
    seconds_reserved: float = Field(default=0, ge=0)
    jobs: int = Field(default=1, ge=1, le=3)
    queries: dict[str, dict] = Field(default_factory=dict, max_length=6)
    plan: list[str] = Field(default_factory=list, max_length=6)
    candidates: dict[str, dict] = Field(default_factory=dict, max_length=100)
    acquisition: dict = Field(default_factory=dict)
    truncated: bool = False

    @model_validator(mode="after")
    def valid_budget(self):
        if self.schema_version != "continuation-v1":
            raise ValueError("unsupported_continuation_version")
        if set(self.caps) != {"queries", "fetches", "llm_calls", "documents"}:
            raise ValueError("invalid_continuation_budget")
        if any(cap < 0 or cap > 100 for cap in self.caps.values()):
            raise ValueError("invalid_continuation_budget")
        if any(k not in self.caps or v < 0 or v > self.caps[k] for k, v in self.used.items()):
            raise ValueError("invalid_continuation_usage")
        return self


class Progress:
    def __init__(self, cursor, persist):
        self.cursor, self.persist = cursor, persist
        self.lock = asyncio.Lock()
        self.start_used = dict(cursor.used)
        self.seen_queries = set()

    async def save(self, session=None):
        async with self.lock:
            await self.persist(session)

    async def charge(self, kind, *, before=None):
        async with self.lock:
            if self.remaining(kind) <= 0:
                return False
            self.cursor.used[kind] = self.cursor.used.get(kind, 0) + 1
            if before:
                before()
            await self.persist(None)
            return True

    def remaining(self, kind):
        return self.cursor.caps[kind] - self.cursor.used.get(kind, 0)

    async def reserve_time(self, timeout):
        async with self.lock:
            granted = min(timeout, max(0, self.cursor.seconds_cap - self.cursor.seconds_reserved))
            self.cursor.seconds_reserved += granted
            await self.persist(None)
            return granted

    def reconcile(self, goal, plan):
        cursor = self.cursor
        if cursor.goal is not None and cursor.goal != digest(goal):
            raise ValueError("continuation_target_changed")
        cursor.goal = digest(goal)
        if cursor.versions != versions():
            # Preserve immutable source pointers and every spent reservation.
            for item in cursor.candidates.values():
                for key in ("done", "terminal", "errors", "applied", "embedded", "summary"):
                    item.pop(key, None)
            cursor.versions = versions()
            cursor.acquisition = {}
        cursor.plan = plan[: min(6, cursor.caps["queries"])]
        cursor.queries = {q: v for q, v in cursor.queries.items() if q in cursor.plan}

    def add(self, results):
        def rank(value):
            query = value["query"]
            return (
                value["position"],
                self.cursor.plan.index(query) if query in self.cursor.plan else 6,
                value["url"],
            )

        for result in results:
            if not usable_url(result.url):
                continue
            key = candidate_key(result.url)
            data = result.model_dump(mode="json", exclude={"snippet"})
            if data.get("title"):
                data["title"] = data["title"][:500]
            if key not in self.cursor.candidates and len(self.cursor.candidates) < 100:
                self.cursor.candidates[key] = {"source": data, "attempts": 0}
            elif key in self.cursor.candidates:
                prior = self.cursor.candidates[key]
                if (
                    not prior.get("document_id")
                    and not prior["attempts"]
                    and rank(data) < rank(prior["source"])
                ):
                    prior["source"] = data
            elif key not in self.cursor.candidates:
                self.cursor.truncated = True

    def pending(self):
        items = [
            item
            for item in self.cursor.candidates.values()
            if not item.get("done") and not item.get("terminal")
        ]

        def ranked(values):
            return diversify(
                sorted(
                    (SearchResult.model_validate(item["source"]) for item in values),
                    key=lambda r: (
                        r.position,
                        self.cursor.plan.index(r.query) if r.query in self.cursor.plan else 6,
                        r.url,
                    ),
                )
            )

        return ranked([i for i in items if i.get("document_id")]) + ranked(
            [i for i in items if not i.get("document_id")]
        )

    def pending_queries(self):
        return [
            q
            for q in self.cursor.plan
            if q not in self.seen_queries
            and not self.cursor.queries.get(q, {}).get("complete")
            and self.cursor.queries.get(q, {}).get("attempts", 0) < 2
        ]

    def has_work(self):
        return bool(
            self.pending()
            or any(
                not self.cursor.queries.get(q, {}).get("complete")
                and self.cursor.queries.get(q, {}).get("attempts", 0) < 2
                for q in self.cursor.plan
            )
        )

    def candidate_can_advance(self, item):
        applied = item.get("applied", {})
        if any(c not in applied for c in item.get("summary", {}).get("claim_ids", [])):
            return True
        if any(
            h not in item.get("embedded", {})
            for value in applied.values()
            for h in value.get("hypothesis_ids", [])
        ):
            return True
        if self.remaining("llm_calls") <= 0:
            return False
        if item.get("document_id"):
            return bool(item.get("charged_document") or self.remaining("documents") > 0)
        return self.remaining("fetches") > 0 and self.remaining("documents") > 0

    def can_advance(self):
        return any(
            self.candidate_can_advance(item)
            for item in self.cursor.candidates.values()
            if not item.get("done") and not item.get("terminal")
        ) or bool(
            self.remaining("llm_calls") > 0
            and self.remaining("queries") > 0
            and self.remaining("fetches") > 0
            and self.remaining("documents") > 0
            and any(
                not self.cursor.queries.get(q, {}).get("complete")
                and self.cursor.queries.get(q, {}).get("attempts", 0) < 2
                for q in self.cursor.plan
            )
        )

    def policy(self):
        policy = AcquisitionPolicy()
        saved = self.cursor.acquisition
        policy.blocked.update(saved.get("blocked", {}))
        for name in ("transient", "empty", "denied"):
            getattr(policy, name).update(saved.get(name, {}))
        return policy

    def remember_policy(self, policy):
        self.cursor.acquisition = {
            name: dict(getattr(policy, name))
            for name in ("blocked", "transient", "empty", "denied")
        }


class SavedSearch:
    def __init__(self, delegate, progress):
        self.delegate, self.progress = delegate, progress

    async def search(self, query):
        progress = self.progress
        item = progress.cursor.queries.setdefault(query, {"attempts": 0})
        if item.get("complete") or item["attempts"] >= 2:
            response = SearchResponse.model_validate(
                {
                    "results": [
                        progress.cursor.candidates[k]["source"]
                        for k in item.get("keys", [])
                        if k in progress.cursor.candidates
                    ],
                    "usage": item.get(
                        "usage", {"requests": 0, "error": "query_attempts_exhausted"}
                    ),
                }
            )
            response.usage.requests, response.usage.reported_credits = 0, 0
            return response
        progress.seen_queries.add(query)
        if not await progress.charge(
            "queries", before=lambda: item.update(attempts=item["attempts"] + 1)
        ):
            return SearchResponse(usage=SearchUsage(requests=0, error="run_query_limit"))
        response = await self.delegate.search(query)
        async with progress.lock:
            progress.add([r.model_copy(update={"query": query}) for r in response.results])
            item.update(
                complete=response.usage.error is None,
                unusable=bool(response.results or response.usage.returned_results)
                and not any(usable_url(r.url) for r in response.results),
                usage=response.usage.model_dump(),
                keys=list(
                    dict.fromkeys(
                        candidate_key(r.url)
                        for r in response.results
                        if candidate_key(r.url) in progress.cursor.candidates
                    )
                ),
            )
            await progress.persist(None)
        return response


class SavedFetch:
    def __init__(self, delegate, progress):
        self.delegate, self.progress = delegate, progress

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def fetch(self, url, **kwargs):
        item = self.progress.cursor.candidates[candidate_key(url)]
        if item["attempts"] >= 2:
            raise FetchError("source_attempts_exhausted")
        if not await self.progress.charge(
            "fetches", before=lambda: item.update(attempts=item["attempts"] + 1)
        ):
            raise FetchError("run_fetch_limit")
        return await self.delegate.fetch(url, **kwargs)
