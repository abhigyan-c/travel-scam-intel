import argparse
import asyncio
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.extraction import extract_document
from app.db.ingestion import ingest_url
from app.db.processing import process_document
from app.db.resolution import resolve_claim, resolve_location
from app.db.session import create_database_engine
from app.discovery import discover, generate_queries
from app.integrations.fetch import Fetcher, FetchError
from app.integrations.sarvam import Completion, Sarvam
from app.integrations.serper import Serper
from app.integrations.storage import create_storage_client
from app.models.document import DocumentStatus
from app.relevance import PROMPT_VERSION, RelevanceResult, classify_text


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


class ResearchLeaseLost(Exception):
    pass


class _LimitedClient:
    def __init__(self, client, limit, concurrency=2):
        self.client, self.limit, self.calls = client, limit, 0
        self.usage = []
        self.semaphore = asyncio.Semaphore(concurrency)

    async def complete(self, **kwargs):
        async with self.semaphore:
            return await self._complete(**kwargs)

    async def _complete(self, **kwargs):
        if self.calls >= self.limit:
            return Completion(error="request_limit")
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
) -> ResearchResult:
    limits = limits or ResearchLimits()
    generate_queries(location)
    target = (
        await resolve_location(session, location, create_provisional=False)
        if target_location_id is None
        else None
    )
    target_id = target_location_id or target.location_id
    deadline = asyncio.get_running_loop().time() + limits.timeout
    errors, documents, claim_ids = [], [], []
    budget = _LimitedClient(llm, limits.llm_calls, limits.llm_concurrency)
    hypothesis_ids, pending_ids = [], []
    discovery = await discover(
        location,
        provider,
        max_queries=limits.queries,
        max_results=limits.candidates,
        timeout=min(25, limits.timeout),
    )
    if discovery.status in {"failed", "partial"}:
        errors.append({"stage": "discovery", "code": discovery.status})

    async def stage(name, url, operation):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            errors.append({"stage": name, "url": url, "code": "deadline"})
            return None
        try:
            async with session.begin_nested():
                async with asyncio.timeout(remaining):
                    value = await operation()
            if checkpoint is not None:
                await checkpoint(session)
            return value
        except ResearchLeaseLost:
            raise
        except Exception as exc:
            code = type(exc).__name__
            if isinstance(exc, TimeoutError):
                code = "deadline"
            elif isinstance(exc, FetchError) and str(exc) in {
                "http_status",
                "fetch_failed",
                "fetch_timeout",
                "non_public_address",
                "unsupported_content_type",
                "response_too_large",
                "insecure_redirect",
            }:
                code = str(exc)
            errors.append({"stage": name, "url": url, "code": code})
            return None

    fetched, eligible = 0, 0
    limited = len(discovery.results) >= limits.candidates
    for candidate in discovery.results:
        if (
            fetched >= limits.fetches
            or eligible >= limits.documents
            or budget.calls >= limits.llm_calls
        ):
            limited = True
            break
        if asyncio.get_running_loop().time() >= deadline:
            limited = True
            break
        fetched += 1
        document = await stage(
            "ingestion",
            candidate.url,
            lambda candidate=candidate: ingest_url(
                session, candidate, fetcher=fetcher, storage=storage, bucket=bucket
            ),
        )
        if document is None:
            continue
        document_id = document.id
        row = {"document_id": str(document_id), "url": candidate.url, "claim_ids": []}
        documents.append(row)
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
        row["processing"] = processing.get("outcome")
        if document.processing_status == DocumentStatus.FAILED:
            errors.append(
                {
                    "stage": "processing",
                    "url": candidate.url,
                    "code": processing.get("outcome", "failed"),
                }
            )
            continue
        if not processing.get("eligible_for_extraction"):
            if document.language is None:
                errors.append(
                    {"stage": "processing", "url": candidate.url, "code": "unknown_language"}
                )
            continue
        eligible += 1
        cached = document.document_metadata.get("relevance")
        decision = None
        if cached:
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
            decision = await stage(
                "relevance",
                candidate.url,
                lambda document=document: classify_text(
                    document.clean_text,
                    budget,
                    max_passages=min(
                        limits.passages, max(1, (limits.llm_calls - budget.calls) // 2)
                    ),
                    timeout=max(0.01, deadline - asyncio.get_running_loop().time() - 0.05),
                ),
            )
            if decision is None:
                continue
            if decision.complete:
                document.document_metadata = {
                    **document.document_metadata,
                    "relevance": decision.model_dump(mode="json"),
                }
                await session.flush()
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
            ),
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
            resolution = await stage(
                "resolution",
                candidate.url,
                lambda identity=identity: resolve_claim(
                    session, identity, parent_id=target_id, create_provisional=False
                ),
            )
            if resolution is not None:
                resolutions.append(
                    {"claim_id": str(identity), **resolution.model_dump(mode="json")}
                )
                if on_claim is not None:
                    outcome = await stage(
                        "hypothesis",
                        candidate.url,
                        lambda identity=identity: on_claim(session, identity, budget),
                    )
                    if outcome:
                        hypothesis_ids.extend(outcome.get("hypothesis_ids", []))
                        pending_ids.extend(outcome.get("pending_resolution_ids", []))
                        if outcome.get("error"):
                            errors.append({"stage": "hypothesis", "code": outcome["error"]})
                        if on_hypothesis is not None:
                            for hypothesis_id in outcome.get("hypothesis_ids", []):
                                embedded = await stage(
                                    "embedding",
                                    candidate.url,
                                    lambda hypothesis_id=hypothesis_id: on_hypothesis(
                                        session, UUID(hypothesis_id)
                                    ),
                                )
                                if embedded and embedded.get("error"):
                                    errors.append({"stage": "embedding", "code": embedded["error"]})
        row["resolutions"] = resolutions
    if limited:
        errors.append({"stage": "research", "code": "resource_limit_or_deadline"})
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
        },
        hypothesis_ids=list(dict.fromkeys(hypothesis_ids)),
        pending_resolution_ids=list(dict.fromkeys(pending_ids)),
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
