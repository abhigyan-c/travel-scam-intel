import argparse
import asyncio
import hashlib
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.acquisition import AcquisitionPolicy, CandidateFetcher
from app.core.config import Settings
from app.db.documents import get_document
from app.db.extraction import extract_document
from app.db.ingestion import ingest_url
from app.db.processing import process_document
from app.db.resolution import resolve_claim, resolve_location
from app.db.session import create_database_engine
from app.discovery import DISCOVERY_VERSION, DiscoveryResponse, discover, generate_queries
from app.integrations.fetch import FETCH_ERROR_CODES, Fetcher, FetchError
from app.integrations.sarvam import MODEL, Completion, Sarvam
from app.integrations.serper import Serper
from app.integrations.storage import create_storage_client
from app.models.document import DocumentStatus
from app.processing import PROCESSING_VERSION
from app.relevance import PROMPT_VERSION, RelevanceResult, classify_text
from app.research_progress import SavedFetch, SavedSearch, candidate_key


class ResearchLimits(BaseModel):
    model_config = ConfigDict(frozen=True)
    queries: int = Field(default=3, ge=1, le=6)
    candidates: int = Field(default=30, ge=1, le=100)
    fetches: int = Field(default=10, ge=1, le=20)
    documents: int = Field(default=6, ge=1, le=10)
    passages: int = Field(default=6, ge=1, le=6)
    llm_calls: int = Field(default=12, ge=1, le=24)
    timeout: float = Field(default=25, gt=0, le=45)
    llm_concurrency: int = Field(default=2, ge=1, le=4)
    fetch_timeout: float = Field(default=6, gt=0, le=10)


class ResearchResult(BaseModel):
    limits: dict
    stage: str = "claims"
    location: str
    location_id: str | None
    status: str
    message: str
    complete: bool
    candidates: int
    fetched: int
    documents: list[dict]
    claim_ids: list[str]
    errors: list[dict]
    usage: dict
    hypothesis_ids: list[str] = Field(default_factory=list)
    pending_resolution_ids: list[str] = Field(default_factory=list)
    diagnostics: dict = Field(default_factory=dict)


class ResearchLeaseLost(Exception):
    pass


class _LimitedClient:
    def __init__(self, client, limit, concurrency=2, reserve=None):
        self.client, self.limit, self.calls = client, limit, 0
        self.usage = []
        self.semaphore = asyncio.Semaphore(concurrency)
        self.reserve = reserve

    async def complete(self, **kwargs):
        async with self.semaphore:
            return await self._complete(**kwargs)

    async def _complete(self, **kwargs):
        if self.calls >= self.limit:
            return Completion(error="request_limit")
        if self.reserve is not None and not await self.reserve("llm_calls"):
            return Completion(error="run_llm_limit")
        self.calls += 1
        try:
            result = await self.client.complete(**kwargs)
        except asyncio.CancelledError:
            self.usage.append({"requests": 1, "error": "cancelled_or_deadline"})
            raise
        self.usage.append(result.model_dump(exclude={"data"}))
        return result


async def research_location(
    session,
    location: str,
    *,
    provider,
    fetcher,
    llm,
    storage,
    bucket: str,
    limits: ResearchLimits | None = None,
    target_location_id: UUID | None = None,
    checkpoint=None,
    on_claim=None,
    on_hypothesis=None,
    topic: str | None = None,
    diagnostics: dict | None = None,
    progress=None,
) -> ResearchResult:
    limits = limits or ResearchLimits()
    generate_queries(location)
    target = (
        await resolve_location(session, location, create_provisional=False)
        if target_location_id is None
        else None
    )
    target_id = target_location_id or target.location_id
    # Leave a small bounded margin for savepoint cleanup and the final checkpoint.
    deadline = asyncio.get_running_loop().time() + limits.timeout - min(0.5, limits.timeout / 10)
    errors, documents, claim_ids = [], [], []
    budget = _LimitedClient(
        llm, limits.llm_calls, limits.llm_concurrency, progress.charge if progress else None
    )
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics.update(stage_timings=[], active_stage="discovery", llm=budget.usage)
    acquisition = progress.policy() if progress else AcquisitionPolicy()
    source_fetcher = SavedFetch(fetcher, progress) if progress else fetcher
    candidate_fetcher = CandidateFetcher(source_fetcher, limits.fetch_timeout, acquisition)
    diagnostics["acquisition"] = acquisition.diagnostics
    started = asyncio.get_running_loop().time()
    hypothesis_ids, pending_ids = [], []
    discovery = (
        await discover(
            location,
            provider,
            max_queries=limits.queries,
            max_results=limits.candidates,
            timeout=min(25, limits.timeout),
            **({"topic": topic} if topic is not None else {}),
        )
        if progress is None
        else DiscoveryResponse(results=[], usage=[], status="ok")
    )
    diagnostics["stage_timings"].append(
        {"stage": "discovery", "seconds": round(asyncio.get_running_loop().time() - started, 3)}
    )
    diagnostics["search"] = [item.model_dump() for item in discovery.usage]
    diagnostics["discovery"] = {
        "version": DISCOVERY_VERSION,
        "filtered": discovery.filtered,
        "truncated": discovery.truncated,
    }
    if discovery.status in {"failed", "partial"}:
        errors.append({"stage": "discovery", "code": discovery.status})

    async def stage(name, url, operation, *, durable=False):
        started = asyncio.get_running_loop().time()
        diagnostics["active_stage"] = name
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            errors.append({"stage": name, "url": url, "code": "deadline"})
            return None
        try:
            if durable:
                async with asyncio.timeout(remaining):
                    value = await operation()
            else:
                async with session.begin_nested():
                    async with asyncio.timeout(remaining):
                        value = await operation()
            if checkpoint is not None:
                if progress:
                    progress.remember_policy(acquisition)
                await checkpoint(session)
            return value
        except ResearchLeaseLost:
            raise
        except Exception as exc:
            if durable:
                await session.rollback()
            code = type(exc).__name__
            if isinstance(exc, TimeoutError):
                code = "deadline"
            elif isinstance(exc, FetchError) and str(exc) in FETCH_ERROR_CODES:
                code = str(exc)
                acquisition.failure(url, exc)
            error = {"stage": name, "url": url, "code": code}
            if isinstance(exc, FetchError) and type(exc.status) is int and 100 <= exc.status <= 599:
                error["http_status"] = exc.status
            errors.append(error)
            if progress:
                progress.remember_policy(acquisition)
                await progress.save(session)
            return None
        finally:
            diagnostics["stage_timings"].append(
                {"stage": name, "seconds": round(asyncio.get_running_loop().time() - started, 3)}
            )
            diagnostics["llm_calls"] = budget.calls

    fetched, eligible = 0, 0
    limited = discovery.truncated
    if progress:
        progress.reconcile(
            {"location_id": str(target_id), "location": location, "topic": topic},
            generate_queries(location, topic=topic),
        )

    async def candidates():
        nonlocal discovery, limited
        if not progress:
            for candidate in discovery.results:
                yield candidate
            return
        for candidate in progress.pending():
            yield candidate
        # Resume existing work before buying any additional discovery.
        if progress.pending() or asyncio.get_running_loop().time() >= deadline:
            return
        plan = progress.pending_queries()
        if plan and progress.remaining("llm_calls") > 0:
            began = asyncio.get_running_loop().time()
            discovery = await discover(
                location,
                SavedSearch(provider, progress),
                max_queries=limits.queries,
                max_results=limits.candidates,
                timeout=max(0.01, deadline - asyncio.get_running_loop().time()),
                query_plan=plan,
            )
            diagnostics["stage_timings"].append(
                {
                    "stage": "discovery",
                    "seconds": round(asyncio.get_running_loop().time() - began, 3),
                }
            )
            diagnostics["search"] = [u.model_dump() for u in discovery.usage]
            limited |= discovery.truncated
            if discovery.status in {"partial", "failed"}:
                errors.append({"stage": "discovery", "code": discovery.status})
            for candidate in progress.pending():
                yield candidate

    examined = 0
    async for candidate in candidates():
        if (
            fetched >= limits.fetches
            or eligible >= limits.documents
            or budget.calls >= limits.llm_calls
            or examined >= limits.candidates
        ):
            limited = True
            break
        examined += 1
        if asyncio.get_running_loop().time() >= deadline:
            limited = True
            break
        if not acquisition.allow(candidate.url):
            if progress:
                item = progress.cursor.candidates[candidate_key(candidate.url)]
                item.update(
                    terminal=True, errors=[{"stage": "acquisition", "code": "host_deferred"}]
                )
                await progress.save()
            continue
        item = progress.cursor.candidates[candidate_key(candidate.url)] if progress else {}
        if progress and not progress.candidate_can_advance(item):
            limited = True
            continue
        error_start = len(errors)
        document = None
        if item.get("document_id"):
            document = await get_document(session, UUID(item["document_id"]))
            if (
                document is None
                or document.content_hash != item.get("content_hash")
                or candidate_key(document.source.canonical_url) != candidate_key(candidate.url)
            ):
                raise ValueError("continuation_document_changed")
        if document is None:
            fetched += 1

            async def ingest_candidate(candidate=candidate, item=item):
                value = await ingest_url(
                    session, candidate, fetcher=candidate_fetcher, storage=storage, bucket=bucket
                )
                if progress:
                    item.update(document_id=str(value.id), content_hash=value.content_hash)
                return value

            document = await stage(
                "ingestion",
                candidate.url,
                ingest_candidate,
            )
        if document is None:
            if progress:
                item["errors"] = errors[error_start:]
                item["terminal"] = item.get("attempts", 0) >= 2
                progress.remember_policy(acquisition)
                await progress.save()
            continue
        document_id = document.id
        row = {"document_id": str(document_id), "url": candidate.url, "claim_ids": []}
        documents.append(row)
        processing = document.document_metadata.get("processing", {})
        reusable = (
            processing.get("version") == PROCESSING_VERSION
            and document.clean_text
            and document.clean_text_hash == hashlib.sha256(document.clean_text.encode()).hexdigest()
        )
        if not reusable:
            document = await stage(
                "processing",
                candidate.url,
                lambda document_id=document_id: process_document(
                    session, document_id, storage=storage, bucket=bucket
                ),
            )
        if document is None:
            continue
        processing = document.document_metadata.get("processing", {})
        acquisition.processed(document.url or candidate.url, processing.get("outcome"))
        row["processing"] = processing.get("outcome")
        if document.processing_status == DocumentStatus.FAILED:
            errors.append(
                {
                    "stage": "processing",
                    "url": candidate.url,
                    "code": processing.get("outcome", "failed"),
                }
            )
            if progress:
                item.update(terminal=True, errors=errors[error_start:])
                progress.remember_policy(acquisition)
                await progress.save()
            continue
        if not processing.get("eligible_for_extraction"):
            if document.language is None:
                errors.append(
                    {"stage": "processing", "url": candidate.url, "code": "unknown_language"}
                )
            if progress:
                item.update(
                    done=document.language is not None,
                    terminal=document.language is None,
                    errors=errors[error_start:],
                )
                await progress.save()
            continue
        if progress and not item.get("charged_document"):
            if not await progress.charge(
                "documents", before=lambda item=item: item.update(charged_document=True)
            ):
                limited = True
                break
        eligible += 1
        cached = document.document_metadata.get("relevance")
        decision = None
        prior = None
        if cached and cached.get("model") == MODEL:
            try:
                prior = RelevanceResult.model_validate(cached)
                if (
                    prior.complete
                    and prior.text_hash == document.clean_text_hash
                    and prior.prompt_version == PROMPT_VERSION
                ):
                    decision = prior
            except ValueError:
                pass
        if decision is None:

            async def save_relevance(value, document=document):
                document.document_metadata = {
                    **document.document_metadata,
                    "relevance": value.model_dump(mode="json"),
                }
                await session.flush()
                await checkpoint(session)

            decision = await stage(
                "relevance",
                candidate.url,
                lambda document=document, prior=prior: classify_text(
                    document.clean_text,
                    budget,
                    max_passages=min(
                        limits.passages, max(1, (limits.llm_calls - budget.calls) // 2)
                    ),
                    # Classification must leave time to extract its completed passages.
                    timeout=max(0.01, (deadline - asyncio.get_running_loop().time()) / 2),
                    prior=prior,
                    checkpoint=save_relevance if checkpoint else None,
                    run_id=progress.cursor.run_id if progress else None,
                ),
                durable=checkpoint is not None,
            )
            if decision is None:
                continue
            if decision.assessments:
                document.document_metadata = {
                    **document.document_metadata,
                    "relevance": decision.model_dump(mode="json"),
                }
                await session.flush()
                if checkpoint is not None:
                    await checkpoint(session)
        row["relevance_complete"] = decision.complete
        row["relevance_passages"] = len(decision.assessments)
        row["relevance_errors"] = [
            item.completion.error for item in decision.assessments if item.completion.error
        ]
        if not decision.complete:
            errors.append({"stage": "relevance", "url": candidate.url, "code": "incomplete"})
        extraction = await stage(
            "extraction",
            candidate.url,
            lambda document_id=document_id, decision=decision: extract_document(
                session,
                document_id,
                decision,
                budget,
                max_passages=limits.passages,
                timeout=max(0.01, deadline - asyncio.get_running_loop().time() - 0.05),
                checkpoint=checkpoint,
            ),
            durable=checkpoint is not None,
        )
        if extraction is None:
            continue
        row["extraction_complete"] = extraction.complete
        row["extraction_attempts"] = extraction.attempts
        row["claim_ids"] = [str(identity) for identity in extraction.claim_ids]
        claim_ids.extend(row["claim_ids"])
        if not extraction.complete:
            errors.append({"stage": "extraction", "url": candidate.url, "code": "incomplete"})
        resolutions = []
        for identity in extraction.claim_ids:
            outcome = item.get("applied", {}).get(str(identity))
            if outcome is None:
                resolution = await stage(
                    "resolution",
                    candidate.url,
                    lambda identity=identity: resolve_claim(
                        session, identity, parent_id=target_id, create_provisional=True
                    ),
                )
                if resolution is None:
                    continue
                resolutions.append(
                    {"claim_id": str(identity), **resolution.model_dump(mode="json")}
                )
                if on_claim is not None:

                    async def apply_claim(identity=identity, item=item):
                        value = await on_claim(session, identity, budget)
                        if (
                            progress
                            and value
                            and value.get("error")
                            in {None, "review_required", "unresolved_location", "claim_not_public"}
                        ):
                            item.setdefault("applied", {})[str(identity)] = value
                        return value

                    outcome = await stage(
                        "hypothesis",
                        candidate.url,
                        apply_claim,
                    )
            if outcome:
                hypothesis_ids.extend(outcome.get("hypothesis_ids", []))
                pending_ids.extend(outcome.get("pending_resolution_ids", []))
                if outcome.get("error"):
                    errors.append({"stage": "hypothesis", "code": outcome["error"]})
                if on_hypothesis is not None:
                    for hypothesis_id in outcome.get("hypothesis_ids", []):
                        if item.get("embedded", {}).get(hypothesis_id):
                            continue

                        async def embed(hypothesis_id=hypothesis_id, item=item):
                            value = await on_hypothesis(session, UUID(hypothesis_id))
                            if progress and value is not None and not value.get("error"):
                                item.setdefault("embedded", {})[hypothesis_id] = True
                            return value

                        embedded = await stage("embedding", candidate.url, embed)
                        if embedded and embedded.get("error"):
                            errors.append({"stage": "embedding", "code": embedded["error"]})
        row["resolutions"] = resolutions
        if progress:
            item.update(summary=row, errors=errors[error_start:], done=not errors[error_start:])
            if (
                decision.complete
                and extraction.complete
                and errors[error_start:]
                and all(
                    e["code"] in {"review_required", "claim_not_public", "unresolved_location"}
                    for e in errors[error_start:]
                )
            ):
                item["terminal"] = True
            progress.remember_policy(acquisition)
            await checkpoint(session)
    deferred = sum(acquisition.diagnostics["deferred"].values())
    if deferred:
        errors.append({"stage": "acquisition", "code": "host_deferred", "count": deferred})
    if limited:
        errors.append({"stage": "research", "code": "resource_limit_or_deadline"})
    if progress:
        documents = [i["summary"] for i in progress.cursor.candidates.values() if i.get("summary")]
        claim_ids = [c for row in documents for c in row.get("claim_ids", [])]
        errors.extend(
            e
            for i in progress.cursor.candidates.values()
            for e in i.get("errors", [])
            if e not in errors
        )
        if (
            progress.has_work()
            or progress.cursor.truncated
            or any(not q.get("complete") for q in progress.cursor.queries.values())
            or any(q.get("unusable") for q in progress.cursor.queries.values())
        ):
            errors.append({"stage": "research", "code": "continuation_pending"})
        await progress.save(session)
    complete = not errors and discovery.status in {"ok", "empty"}
    status = (
        ("ok" if claim_ids else "empty") if complete else ("partial" if documents else "failed")
    )
    message = {
        "ok": "Claims are available.",
        "empty": "Nothing was found.",
        "partial": "Research is incomplete; available results are retained.",
        "failed": "Research could not complete.",
    }[status]
    return ResearchResult(
        limits=limits.model_dump(),
        location=location,
        location_id=str(target_id) if target_id else None,
        status=status,
        message=message,
        complete=complete,
        candidates=len(discovery.results),
        fetched=fetched,
        documents=documents,
        claim_ids=list(dict.fromkeys(claim_ids)),
        errors=errors,
        usage={
            "search": [item.model_dump() for item in discovery.usage],
            "llm": budget.usage,
            "llm_calls": budget.calls,
            **(
                {
                    "run_reservations": progress.cursor.used,
                    "reservation_note": "Pre-call reservations; interrupted usage may be unknown.",
                }
                if progress
                else {}
            ),
        },
        hypothesis_ids=list(dict.fromkeys(hypothesis_ids)),
        pending_resolution_ids=list(dict.fromkeys(pending_ids)),
        diagnostics=diagnostics,
    )


async def _main(args):
    settings = Settings()
    engine = create_database_engine(settings)
    storage = create_storage_client(settings)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            async with session.begin():
                result = await research_location(
                    session,
                    args.location,
                    provider=Serper(settings.serper_api_key),
                    fetcher=Fetcher(),
                    llm=Sarvam(settings.sarvam_api_key),
                    storage=storage,
                    bucket=settings.s3_bucket,
                    limits=ResearchLimits(
                        queries=args.queries,
                        fetches=args.fetches,
                        documents=args.documents,
                        llm_calls=args.llm_calls,
                        timeout=args.timeout,
                    ),
                )
            print(result.model_dump_json(indent=2))
    finally:
        storage.close()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Research a location through persisted claims")
    parser.add_argument("location")
    parser.add_argument("--queries", type=int, default=3)
    parser.add_argument("--fetches", type=int, default=10)
    parser.add_argument("--documents", type=int, default=6)
    parser.add_argument("--llm-calls", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=25)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_main(parser.parse_args()))
