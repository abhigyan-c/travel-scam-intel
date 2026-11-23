import asyncio
import hashlib
import unicodedata
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.documents import get_document
from app.extraction import PROMPT_VERSION, extract_passage
from app.integrations.sarvam import MODEL, Sarvam
from app.models import Claim, ClaimExtraction, Document
from app.models.document import DocumentStatus
from app.relevance import RelevanceResult


class DocumentExtraction(BaseModel):
    claim_ids: list[UUID]
    complete: bool
    attempts: list[dict]


def _timestamp(value: str | None):
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


async def extract_document(
    session: AsyncSession,
    document_id: UUID,
    relevance: RelevanceResult,
    client: Sarvam,
    *,
    timeout: float = 45,
    max_passages: int = 6,
    checkpoint=None,
) -> DocumentExtraction:
    if timeout <= 0 or not 1 <= max_passages <= 20:
        raise ValueError("invalid extraction deadline")
    document = await get_document(session, document_id)
    if document is None:
        raise ValueError("document_not_found")
    if (
        document.processing_status != DocumentStatus.PROCESSED
        or document.language != "en"
        or not document.clean_text
    ):
        raise ValueError("document_not_eligible")
    source_text = document.clean_text
    text_hash = hashlib.sha256(source_text.encode()).hexdigest()
    if document.clean_text_hash != text_hash or relevance.text_hash != text_hash:
        raise ValueError("stale_relevance")
    for assessment in relevance.assessments:
        passage = assessment.passage
        if (
            not 0 <= passage.start < passage.end <= len(document.clean_text)
            or passage.text != document.clean_text[passage.start : passage.end]
        ):
            raise ValueError("invalid_passage_lineage")
    claim_ids, attempts = [], []
    complete = relevance.complete
    deadline = asyncio.get_running_loop().time() + timeout
    attempted = 0

    async def locked_document():
        current = await session.scalar(
            select(Document)
            .where(Document.id == document_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            current is None
            or current.clean_text_hash != text_hash
            or current.clean_text != source_text
            or current.processing_status != DocumentStatus.PROCESSED
            or current.language != "en"
        ):
            raise ValueError("document_changed_during_extraction")
        return current

    for assessment in relevance.assessments:
        if assessment.completion.error is not None or assessment.completion.data is None:
            complete = False
            continue
        if not assessment.completion.data.relevant:
            continue
        passage = assessment.passage
        criteria = (
            ClaimExtraction.document_id == document_id,
            ClaimExtraction.clean_text_hash == text_hash,
            ClaimExtraction.model == MODEL,
            ClaimExtraction.prompt_version == PROMPT_VERSION,
            ClaimExtraction.passage_start == passage.start,
            ClaimExtraction.passage_end == passage.end,
        )
        run = await session.scalar(select(ClaimExtraction).where(*criteria))
        fresh = run is None
        if run is None:
            if attempted >= max_passages:
                complete = False
                continue
            attempted += 1
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                complete = False
                break
            review_key = hashlib.sha256(
                f"{MODEL}:{PROMPT_VERSION}:{text_hash}:{passage.start}:{passage.end}".encode()
            ).hexdigest()
            pending = document.document_metadata.get("empty_extraction_reviews", {}).get(review_key)
            if pending and pending.get("state") == "failed":
                attempts.append(
                    {
                        "passage_start": passage.start,
                        "cached": True,
                        "requests": 0,
                        "error": pending.get("error", "invalid_grounding"),
                    }
                )
                complete = False
                continue
            try:
                async with asyncio.timeout(remaining):
                    if not pending:
                        result = await extract_passage(passage, client)
                        attempts.append(
                            {
                                "passage_start": passage.start,
                                "cached": False,
                                "purpose": "extraction",
                                **result.model_dump(exclude={"data"}),
                            }
                        )
                        invalid_entity = result.error == "invalid_entity_contract"
                        invalid_grounding = result.error == "invalid_grounding" and bool(
                            getattr(result, "validation_errors", [])
                        )
                        if not (invalid_entity or invalid_grounding) and (
                            result.error is not None or result.data is None
                        ):
                            complete = False
                            continue
                        if invalid_entity or invalid_grounding or not result.data.claims:
                            current = await locked_document()
                            saved = {
                                key: value
                                for key, value in current.document_metadata.get(
                                    "empty_extraction_reviews", {}
                                ).items()
                                if value.get("text_hash") == text_hash
                                and value.get("version") == PROMPT_VERSION
                            }
                            pending = dict(
                                version=PROMPT_VERSION,
                                text_hash=text_hash,
                                model=MODEL,
                                state="pending",
                                usage=result.usage,
                                reason="invalid_entity"
                                if invalid_entity
                                else "invalid_grounding"
                                if invalid_grounding
                                else "empty",
                                invalid_fields=getattr(result, "validation_errors", []),
                                preserve_hold=getattr(result, "preserve_hold", False),
                            )
                            saved[review_key] = pending
                            current.document_metadata = {
                                **current.document_metadata,
                                "empty_extraction_reviews": saved,
                            }
                            await session.flush()
                            if checkpoint is not None:
                                await checkpoint(session)
                    if pending:
                        result = await extract_passage(
                            passage,
                            client,
                            review_empty=True,
                            invalid_entity=pending.get("reason") == "invalid_entity",
                            invalid_fields=pending.get("invalid_fields")
                            if pending.get("reason") == "invalid_grounding"
                            else None,
                        )
                        attempts.append(
                            {
                                "passage_start": passage.start,
                                "cached": False,
                                "purpose": "empty_review",
                                **result.model_dump(exclude={"data"}),
                            }
                        )
            except TimeoutError:
                attempts.append({"passage_start": passage.start, "error": "deadline"})
                complete = False
                break
            if result.error is not None or result.data is None:
                if pending and result.error in {"invalid_grounding", "invalid_entity_contract"}:
                    current = await locked_document()
                    reviews = dict(current.document_metadata["empty_extraction_reviews"])
                    reviews[review_key] = {**pending, "state": "failed", "error": result.error}
                    current.document_metadata = {
                        **current.document_metadata,
                        "empty_extraction_reviews": reviews,
                    }
                    await session.flush()
                    if checkpoint is not None:
                        await checkpoint(session)
                complete = False
                continue
            # Verify the input again under a lock before committing derived claims.
            current = await locked_document()
            usage = dict(result.usage)
            if pending:
                for key, value in pending.get("usage", {}).items():
                    usage[key] = usage.get(key, 0) + value
                reviews = dict(current.document_metadata["empty_extraction_reviews"])
                reviews[review_key] = {**pending, "state": "completed"}
                current.document_metadata = {
                    **current.document_metadata,
                    "empty_extraction_reviews": reviews,
                }
            async with session.begin_nested():
                prior_holds = list(
                    await session.scalars(
                        select(Claim).where(
                            Claim.document_id == document_id,
                            Claim.structured_data["publication_status"].astext == "held",
                        )
                    )
                )
                run_id = await session.scalar(
                    insert(ClaimExtraction)
                    .values(
                        document_id=document_id,
                        clean_text_hash=text_hash,
                        model=MODEL,
                        prompt_version=PROMPT_VERSION,
                        passage_start=passage.start,
                        passage_end=passage.end,
                        passage_text=passage.text,
                        claim_count=len(result.data.claims),
                        usage=usage,
                        request_id=(result.request_id or "")[:200] or None,
                    )
                    .on_conflict_do_nothing(constraint="uq_claim_extraction_passage")
                    .returning(ClaimExtraction.id)
                )
                if run_id is not None:
                    for index, extracted in enumerate(result.data.claims):
                        quote_start = passage.start + passage.text.index(extracted.evidence_quote)
                        held = (
                            extracted.sensitive
                            or bool(extracted.entities)
                            or bool(pending and pending.get("preserve_hold"))
                        )
                        inherited = []
                        for prior in prior_holds:
                            old = prior.structured_data
                            lineage = old.get("extraction", {})
                            start, end = lineage.get("quote_start"), lineage.get("quote_end")
                            overlap = (
                                lineage.get("clean_text_hash") == text_hash
                                and type(start) is int
                                and type(end) is int
                                and start < quote_start + len(extracted.evidence_quote)
                                and quote_start < end
                            )
                            quote = old.get("evidence_quote")
                            same_quote = (
                                isinstance(quote, str)
                                and bool(quote)
                                and (
                                    quote in extracted.evidence_quote
                                    or extracted.evidence_quote in quote
                                )
                            )
                            if overlap or same_quote:
                                inherited.append(str(prior.id))
                        held = held or bool(inherited)
                        normalized = " ".join(
                            unicodedata.normalize("NFKC", extracted.claim).split()
                        ).casefold()
                        session.add(
                            Claim(
                                document_id=document_id,
                                extraction_id=run_id,
                                claim_index=index,
                                claim_text=extracted.claim,
                                normalized_claim=normalized,
                                claim_type=assessment.completion.data.category
                                or "reported_pattern",
                                extraction_confidence=extracted.confidence,
                                occurred_at=_timestamp(extracted.occurred_at),
                                structured_data={
                                    **extracted.model_dump(),
                                    "unresolved_location": extracted.location,
                                    "publication_status": "held" if held else "unreviewed",
                                    "sensitive": held,
                                    **({"hold_inherited_from": inherited} if inherited else {}),
                                    "extraction": {
                                        "model": MODEL,
                                        "prompt_version": PROMPT_VERSION,
                                        "relevance_prompt_version": relevance.prompt_version,
                                        "clean_text_hash": text_hash,
                                        "passage_start": passage.start,
                                        "passage_end": passage.end,
                                        "quote_start": quote_start,
                                        "quote_end": quote_start + len(extracted.evidence_quote),
                                    },
                                },
                            )
                        )
                    await session.flush()
                run = await session.scalar(select(ClaimExtraction).where(*criteria))
        else:
            attempts.append({"passage_start": passage.start, "cached": True, "requests": 0})
        stored = await session.scalars(
            select(Claim.id).where(Claim.extraction_id == run.id).order_by(Claim.claim_index)
        )
        claim_ids.extend(stored)
        if fresh and checkpoint is not None:
            await checkpoint(session)
    return DocumentExtraction(
        claim_ids=list(dict.fromkeys(claim_ids)), complete=complete, attempts=attempts
    )
