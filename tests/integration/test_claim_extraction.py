import hashlib
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.db.documents import create_document, set_document_processing
from app.db.extraction import extract_document
from app.extraction import PROMPT_VERSION, ClaimBatch, ExtractedClaim
from app.integrations.sarvam import Completion
from app.models import Claim, ClaimExtraction, Document, Source
from app.models.document import DocumentStatus
from app.models.source import SourceType
from app.relevance import Passage, PassageAssessment, Relevance, RelevanceResult

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]
CASE = json.loads((Path(__file__).parents[1] / "fixtures/extraction_cases.json").read_text())[0]


@pytest.fixture
async def document(session):
    source = Source(platform="fixture", source_type=SourceType.USER_REPORT)
    session.add(source)
    await session.flush()
    document = await create_document(
        session,
        source_id=source.id,
        raw_object_path="raw/fixture",
        content_hash=hashlib.sha256(CASE["text"].encode()).hexdigest(),
    )
    return await set_document_processing(
        session,
        document.id,
        processing_status=DocumentStatus.PROCESSED,
        clean_text=CASE["text"],
        language="en",
        document_metadata={"processing": {"eligible_for_extraction": True}},
    )


def relevance(document, *, relevant=True):
    text = document.clean_text
    return RelevanceResult(
        text_hash=document.clean_text_hash,
        complete=True,
        assessments=[
            PassageAssessment(
                passage=Passage(index=0, start=0, end=len(text), text=text),
                completion=Completion(
                    data=Relevance(
                        relevant=relevant,
                        category="fare" if relevant else None,
                        confidence=0.9,
                        reason="Fixture relevance",
                    )
                ),
            )
        ],
    )


def client_with_claim():
    client = AsyncMock()
    client.complete.return_value = Completion(
        data=ClaimBatch(claims=[ExtractedClaim.model_validate(CASE["claim"])]),
        requests=1,
        usage={"prompt_tokens": 100, "completion_tokens": 150},
        request_id="fixture-request",
    )
    return client


async def test_persistence_lineage_sensitive_hold_and_retry_checkpoint(session, document):
    client = client_with_claim()
    decision = relevance(document)
    result = await extract_document(session, document.id, decision, client)
    assert result.complete and len(result.claim_ids) == 1
    stored = await session.scalar(
        select(Claim)
        .where(Claim.id == result.claim_ids[0])
        .options(joinedload(Claim.document).joinedload(Document.source))
    )
    assert stored.document.source.source_type == SourceType.USER_REPORT
    assert stored.document_id == document.id and stored.location_id is None
    assert stored.structured_data["unresolved_location"] == "Bangkok"
    assert stored.structured_data["first_hand"] is True
    assert stored.structured_data["sensitive"] is True
    assert stored.structured_data["publication_status"] == "held"
    assert stored.occurred_at.isoformat() == "2026-01-02T10:00:00+00:00"
    lineage = stored.structured_data["extraction"]
    assert (
        document.clean_text[lineage["quote_start"] : lineage["quote_end"]]
        == CASE["claim"]["evidence_quote"]
    )
    run = await session.get(ClaimExtraction, stored.extraction_id)
    assert run.passage_text == document.clean_text and run.claim_count == 1
    assert run.model == "sarvam-105b" and run.prompt_version == PROMPT_VERSION
    assert run.usage["completion_tokens"] == 150
    repeated = await extract_document(session, document.id, decision, client)
    assert repeated.claim_ids == result.claim_ids and repeated.attempts[0]["cached"]
    client.complete.assert_awaited_once()
    assert (
        await session.scalar(
            select(func.count())
            .select_from(ClaimExtraction)
            .where(ClaimExtraction.document_id == document.id)
        )
        == 1
    )


async def test_irrelevant_passages_make_no_extraction_calls(session, document):
    client = client_with_claim()
    result = await extract_document(
        session, document.id, relevance(document, relevant=False), client
    )
    assert result.complete and result.claim_ids == []
    client.complete.assert_not_called()


async def test_old_empty_extraction_is_not_reused_after_prompt_version_change(session, document):
    old = ClaimExtraction(
        document_id=document.id,
        clean_text_hash=document.clean_text_hash,
        model="sarvam-105b",
        prompt_version="claims-v2",
        passage_start=0,
        passage_end=len(document.clean_text),
        passage_text=document.clean_text,
        claim_count=0,
    )
    session.add(old)
    await session.flush()
    client = client_with_claim()
    result = await extract_document(session, document.id, relevance(document), client)
    assert result.complete and len(result.claim_ids) == 1
    assert old.claim_count == 0
    assert (
        await session.scalar(
            select(func.count())
            .select_from(ClaimExtraction)
            .where(ClaimExtraction.document_id == document.id)
        )
        == 2
    )
    repeated = await extract_document(session, document.id, relevance(document), client)
    assert repeated.claim_ids == result.claim_ids and repeated.attempts[0]["cached"]
    client.complete.assert_awaited_once()


async def test_grounding_field_diagnostics_persist_without_rejected_source_values(
    session, document
):
    client = client_with_claim()
    invalid = ExtractedClaim.model_validate({**CASE["claim"], "methods": ["invented method"]})
    client.complete.return_value = Completion(data=ClaimBatch(claims=[invalid]), requests=1)
    result = await extract_document(session, document.id, relevance(document), client)
    assert not result.complete and not result.claim_ids
    assert result.attempts[0]["validation_errors"] == ["methods"]
    assert "invented method" not in str(result.attempts)


async def test_empty_success_cached_but_provider_errors_retry(session, document):
    client = AsyncMock()
    client.complete.return_value = Completion(error="rate_limited", requests=1)
    failed = await extract_document(session, document.id, relevance(document), client)
    assert not failed.complete and failed.claim_ids == []
    assert (
        await session.scalar(
            select(func.count())
            .select_from(ClaimExtraction)
            .where(ClaimExtraction.document_id == document.id)
        )
        == 0
    )
    client.complete.return_value = Completion(data=ClaimBatch(claims=[]), requests=1)
    empty = await extract_document(session, document.id, relevance(document), client)
    assert empty.complete and empty.claim_ids == []
    await extract_document(session, document.id, relevance(document), client)
    assert client.complete.await_count == 2


async def test_stale_relevance_and_forged_offsets_rejected_before_calls(session, document):
    client = client_with_claim()
    stale = relevance(document)
    stale.text_hash = "0" * 64
    with pytest.raises(ValueError, match="stale_relevance"):
        await extract_document(session, document.id, stale, client)
    forged = relevance(document)
    forged.assessments[0].passage.text = "invented source text"
    with pytest.raises(ValueError, match="passage_lineage"):
        await extract_document(session, document.id, forged, client)
    client.complete.assert_not_called()


async def test_document_changed_during_call_has_no_claim_writes(session, document):
    client = client_with_claim()
    response = client.complete.return_value

    async def mutate(**kwargs):
        await session.execute(
            update(Document).where(Document.id == document.id).values(clean_text="changed")
        )
        return response

    client.complete.side_effect = mutate
    with pytest.raises(ValueError, match="document_changed"):
        await extract_document(session, document.id, relevance(document), client)
    assert (
        await session.scalar(
            select(func.count())
            .select_from(ClaimExtraction)
            .where(ClaimExtraction.document_id == document.id)
        )
        == 0
    )


async def test_relational_lineage_rejects_wrong_document_and_duplicate_indices(session, document):
    result = await extract_document(session, document.id, relevance(document), client_with_claim())
    stored = await session.get(Claim, result.claim_ids[0])
    other = await create_document(
        session, source_id=document.source_id, raw_object_path="raw/other", content_hash="b" * 64
    )
    for document_id, index in [(other.id, 1), (document.id, 0), (document.id, -1)]:
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                session.add(
                    Claim(
                        document_id=document_id,
                        extraction_id=stored.extraction_id,
                        claim_index=index,
                        claim_text="Fixture",
                        normalized_claim="fixture",
                        claim_type="fare",
                    )
                )
                await session.flush()
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                delete(ClaimExtraction).where(ClaimExtraction.id == stored.extraction_id)
            )


async def test_unresolved_report_has_no_invented_time_or_first_hand_flag(session, document):
    case = json.loads((Path(__file__).parents[1] / "fixtures/extraction_cases.json").read_text())[1]
    await set_document_processing(
        session,
        document.id,
        processing_status=DocumentStatus.PROCESSED,
        clean_text=case["text"],
        language="en",
        document_metadata={},
    )
    client = AsyncMock()
    client.complete.return_value = Completion(
        data=ClaimBatch(claims=[ExtractedClaim.model_validate(case["claim"])])
    )
    result = await extract_document(session, document.id, relevance(document), client)
    stored = await session.get(Claim, result.claim_ids[0])
    assert stored.occurred_at is None and stored.structured_data["first_hand"] is None
    assert (
        stored.location_id is None
        and stored.structured_data["unresolved_location"] == "the old station"
    )
    assert stored.structured_data["publication_status"] == "unreviewed"


async def test_non_english_document_never_reaches_extraction(session, document):
    document.language = "fr"
    await session.flush()
    client = client_with_claim()
    with pytest.raises(ValueError, match="not_eligible"):
        await extract_document(session, document.id, relevance(document), client)
    client.complete.assert_not_called()


async def test_changed_clean_text_creates_new_run_and_keeps_old_claims(session, document):
    client = client_with_claim()
    first = await extract_document(session, document.id, relevance(document), client)
    await set_document_processing(
        session,
        document.id,
        processing_status=DocumentStatus.PROCESSED,
        clean_text=CASE["text"] + " Additional source context.",
        language="en",
        document_metadata={},
    )
    second = await extract_document(session, document.id, relevance(document), client)
    assert first.claim_ids != second.claim_ids
    assert await session.get(Claim, first.claim_ids[0]) is not None
    assert (
        await session.scalar(
            select(func.count())
            .select_from(ClaimExtraction)
            .where(ClaimExtraction.document_id == document.id)
        )
        == 2
    )
