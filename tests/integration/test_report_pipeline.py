import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from arq import Retry
from botocore.exceptions import EndpointConnectionError
from sqlalchemy import func, select
from test_on_demand_research import (
    environment as environment,
)  # isolated schema and controlled providers

from app.integrations.sarvam import Completion
from app.main import create_app
from app.models import Claim, Document, Evidence, Hypothesis, ResearchJob, Source, UserReport
from app.on_demand import get_location_intelligence
from app.worker import run_job

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]

BODY = (
    "In Fiji, I agreed to a fixed fare for my trip, but later the driver demanded extra money "
    "on 2026-01-02T10:00:00+00:00. I refused to pay more than the agreed price."
)


@pytest.fixture
async def api(environment):
    ctx, location_id, extracted = environment
    application = create_app(ctx["settings"])
    application.state.db_sessions = ctx["sessions"]
    application.state.storage = ctx["storage"]
    application.state.report_queue = ctx["redis"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        yield client, ctx, location_id, extracted


def payload(location_id, **changes):
    return {
        "report_text": BODY,
        "provenance_class": "FIRST_HAND",
        "location_id": str(location_id),
        **changes,
    }


async def submit(api, **changes):
    client, ctx, location_id, extracted = api
    extracted.evidence_quote = BODY
    response = await client.post(
        "/v1/reports",
        json=payload(location_id, **changes),
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 202, response.text
    async with ctx["sessions"]() as session:
        report = await session.get(UserReport, UUID(response.json()["report_id"]))
        return response, report


async def test_anonymous_acceptance_is_durable_normalized_and_has_no_inline_provider_calls(api):
    response, report = await submit(api)
    _, ctx, _, _ = api
    assert response.json()["status"] == "queued"
    ctx["llm"].complete.assert_not_called()
    async with ctx["sessions"]() as session:
        document = await session.get(Document, report.document_id)
        source = await session.get(Source, document.source_id)
        assert source.source_type.value == "user_report" and source.author_identifier is None
        assert document.clean_text == report.report_text == BODY
        assert document.language == "en" and document.clean_text_hash
        assert document.raw_object_path.startswith("reports/")
        job = await session.get(ResearchJob, report.processing_job_id)
        assert job.payload == {"report_id": str(report.id)}
        assert BODY not in json.dumps(job.payload)
    await run_job(ctx, str(report.processing_job_id))
    async with ctx["sessions"]() as session:
        final = await session.get(UserReport, report.id)
        assert final.processing_status == final.moderation_status == "processed"
        assert await session.scalar(select(func.count()).select_from(Hypothesis)) == 1
        assert await session.scalar(select(func.count()).select_from(Evidence)) == 1


async def test_external_and_user_reports_converge_into_one_hypothesis_with_distinct_provenance(api):
    _, ctx, location_id, _ = api
    external = await get_location_intelligence(ctx, location_id)
    assert len(external.hypotheses) == 1
    _, report = await submit(api, provenance_class="SECOND_HAND")
    await run_job(ctx, str(report.processing_job_id))
    async with ctx["sessions"]() as session:
        rows = (
            await session.execute(
                select(Evidence, Claim, Document, Source)
                .join(Claim, Evidence.claim_id == Claim.id)
                .join(Document, Claim.document_id == Document.id)
                .join(Source, Document.source_id == Source.id)
            )
        ).all()
        assert len(rows) == 2
        assert {str(row.Evidence.hypothesis_id) for row in rows} == {external.hypotheses[0]["id"]}
        assert {row.Source.source_type.value for row in rows} == {"other", "user_report"}
        assert len({row.Document.id for row in rows}) == 2
        submitted = next(row for row in rows if row.Document.id == report.document_id)
        assert submitted.Claim.structured_data["first_hand"] is True
        assert submitted.Evidence.weight_components["provenance"] == "SECOND_HAND"
        assert submitted.Evidence.source_quality_score == 0.4
        assert submitted.Evidence.weight_run_id is not None
        assert await session.scalar(select(func.count()).select_from(Hypothesis)) == 1


async def test_same_key_concurrent_requests_create_one_receipt_document_and_job(api):
    client, ctx, identity, _ = api
    key = str(uuid4())
    responses = await asyncio.gather(
        *(
            client.post("/v1/reports", json=payload(identity), headers={"Idempotency-Key": key})
            for _ in range(4)
        )
    )
    assert {response.status_code for response in responses} == {202}
    assert len({response.json()["report_id"] for response in responses}) == 1
    assert sum(not response.json()["replayed"] for response in responses) == 1
    async with ctx["sessions"]() as session:
        for model in (UserReport, Document, ResearchJob):
            assert await session.scalar(select(func.count()).select_from(model)) == 1
    assert ctx["storage"].put_object.call_count == 1
    conflict = await client.post(
        "/v1/reports",
        json=payload(identity, provenance_class="WITNESSED"),
        headers={"Idempotency-Key": key},
    )
    assert conflict.status_code == 409 and BODY not in conflict.text


async def test_pii_stays_private_sanitized_and_held_before_any_model_call(api):
    response, report = await submit(api, report_text=BODY + " Contact me at traveler@example.com.")
    _, ctx, _, _ = api
    assert response.json()["status"] == "needs_review" and report.processing_job_id is None
    assert "traveler@example.com" not in report.report_text
    raw = ctx["storage"].put_object.call_args.kwargs["Body"]
    assert b"traveler@example.com" in raw  # Private immutable raw submission only.
    assert "traveler@example.com" not in response.text
    ctx["llm"].complete.assert_not_called()


async def test_sensitive_extracted_claim_is_retained_without_public_evidence(api):
    _, ctx, _, extracted = api
    extracted.sensitive = True
    _, report = await submit(api)
    await run_job(ctx, str(report.processing_job_id))
    async with ctx["sessions"]() as session:
        stored = await session.get(UserReport, report.id)
        assert stored.processing_status == stored.moderation_status == "needs_review"
        assert await session.scalar(select(func.count()).select_from(Claim)) == 1
        assert await session.scalar(select(func.count()).select_from(Evidence)) == 0


async def test_provider_failure_retries_without_fabricating_claims_then_stops(api):
    _, report = await submit(api)
    _, ctx, _, _ = api
    ctx["llm"].complete.side_effect = None
    ctx["llm"].complete.return_value = Completion(error="rate_limited", requests=1)
    for _ in range(2):
        with pytest.raises(Retry):
            await run_job(ctx, str(report.processing_job_id))
    await run_job(ctx, str(report.processing_job_id))
    async with ctx["sessions"]() as session:
        assert (await session.get(ResearchJob, report.processing_job_id)).status.value == "failed"
        stored = await session.get(UserReport, report.id)
        assert (
            stored.processing_status == "partial"
            and "rate_limited" in stored.processing_details["error_codes"]
        )
        assert await session.scalar(select(func.count()).select_from(Claim)) == 0


async def test_queue_outage_preserves_accepted_submission_for_dispatch(api):
    _, ctx, _, _ = api
    ctx["redis"].enqueue_job.side_effect = ConnectionError("fixture")
    response, report = await submit(api)
    assert response.json()["queue_pending"] and report.processing_job_id
    ctx["redis"].enqueue_job.side_effect = None
    await run_job(ctx, str(report.processing_job_id))
    async with ctx["sessions"]() as session:
        assert (await session.get(UserReport, report.id)).processing_status == "processed"


async def test_storage_failure_does_not_accept_a_report_or_create_a_job(api):
    client, ctx, identity, _ = api
    ctx["storage"].put_object.side_effect = EndpointConnectionError(endpoint_url="fixture")
    result = await client.post(
        "/v1/reports", json=payload(identity), headers={"Idempotency-Key": str(uuid4())}
    )
    assert result.status_code == 503
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(UserReport)) == 0
        assert await session.scalar(select(func.count()).select_from(ResearchJob)) == 0


async def test_unresolved_explicit_context_is_held_without_guessing_a_location(api):
    client, ctx, _, _ = api
    result = await client.post(
        "/v1/reports",
        json={
            "report_text": BODY,
            "provenance_class": "FIRST_HAND",
            "location_text": "An unknown place",
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert result.status_code == 202 and result.json()["status"] == "needs_review"
    ctx["llm"].complete.assert_not_called()


async def test_validation_requires_key_and_does_not_echo_submitted_pii(api):
    client, _, identity, _ = api
    missing = await client.post("/v1/reports", json=payload(identity))
    assert missing.status_code == 422
    bad = await client.post(
        "/v1/reports",
        json=payload(identity, report_text="private@example.com", moderation_status="accepted"),
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert bad.status_code == 422 and "private@example.com" not in bad.text


async def test_context_can_create_a_provisional_location_without_guessing_country(api):
    client, ctx, identity, extracted = api
    result = await client.post(
        "/v1/reports",
        json={
            "report_text": BODY,
            "provenance_class": "WITNESSED",
            "location_text": "New Report Quarter",
            "parent_id": str(identity),
            "location_type": "neighborhood",
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert result.status_code == 202
    async with ctx["sessions"]() as session:
        from app.models import Location

        report = await session.get(UserReport, UUID(result.json()["report_id"]))
        place = await session.get(Location, report.location_id)
        assert place.is_provisional and place.parent_id == identity and place.country_code == "FJ"


async def test_missing_narrative_place_can_use_explicit_submission_context(api):
    _, ctx, identity, extracted = api
    extracted.location = None
    _, report = await submit(api)
    await run_job(ctx, str(report.processing_job_id))
    async with ctx["sessions"]() as session:
        claim = await session.scalar(select(Claim))
        assert claim.location_id == identity
        assert claim.structured_data["location_resolution"]["method"] == "submission_context"


async def test_ordinary_semantic_new_report_can_be_processed_as_unverified_candidate(api):
    _, ctx, identity, extracted = api
    await get_location_intelligence(ctx, identity)
    extracted.claim = "A different reported fare negotiation pattern."
    _, report = await submit(api)
    await run_job(ctx, str(report.processing_job_id))
    async with ctx["sessions"]() as session:
        stored = await session.get(UserReport, report.id)
        assert stored.processing_status == stored.moderation_status == "processed"
        assert "review_required" not in stored.processing_details.get("error_codes", [])
        assert await session.scalar(select(func.count()).select_from(Hypothesis)) == 2
        assert await session.scalar(select(func.count()).select_from(Evidence)) == 2


async def test_semantic_report_attachment_to_existing_pattern_still_requires_review(api):
    from app.hypothesis_resolution import Decision

    _, ctx, identity, extracted = api
    initial = await get_location_intelligence(ctx, identity)
    extracted.claim = "The same fare mechanism described in different wording."
    original = ctx["llm"].complete.side_effect

    async def complete(**kwargs):
        value = await original(**kwargs)
        if kwargs["schema"] is Decision:
            value.data.decision = "SAME"
            value.data.target_id = str(initial.hypotheses[0]["id"])
        return value

    ctx["llm"].complete.side_effect = complete
    _, report = await submit(api)
    await run_job(ctx, str(report.processing_job_id))
    async with ctx["sessions"]() as session:
        stored = await session.get(UserReport, report.id)
        assert stored.processing_status == stored.moderation_status == "needs_review"
        assert "review_required" in stored.processing_details["error_codes"]
        assert await session.scalar(select(func.count()).select_from(Hypothesis)) == 1


async def test_receipt_replay_and_job_redelivery_do_not_repeat_processing(api):
    client, ctx, identity, extracted = api
    extracted.evidence_quote = BODY
    key = str(uuid4())
    first = await client.post(
        "/v1/reports", json=payload(identity), headers={"Idempotency-Key": key}
    )
    async with ctx["sessions"]() as session:
        report = await session.get(UserReport, UUID(first.json()["report_id"]))
    await run_job(ctx, str(report.processing_job_id))
    calls = ctx["llm"].complete.await_count
    again = await client.post(
        "/v1/reports", json=payload(identity), headers={"Idempotency-Key": key}
    )
    assert again.json()["replayed"] and again.json()["status"] == "processed"
    await run_job(ctx, str(report.processing_job_id))
    assert ctx["llm"].complete.await_count == calls


async def test_irrelevant_complete_submission_does_not_invent_a_hypothesis(api):
    from app.extraction import ClaimBatch
    from app.relevance import Relevance

    _, ctx, _, _ = api

    async def complete(**kwargs):
        if kwargs["schema"] is Relevance:
            return Completion(
                data=Relevance(
                    relevant=False, category=None, confidence=1, reason="Ordinary travel report"
                ),
                requests=1,
            )
        return Completion(data=ClaimBatch(claims=[]), requests=1)

    ctx["llm"].complete.side_effect = complete
    _, report = await submit(api)
    await run_job(ctx, str(report.processing_job_id))
    async with ctx["sessions"]() as session:
        assert (await session.get(UserReport, report.id)).processing_status == "processed"
        assert await session.scalar(select(func.count()).select_from(Hypothesis)) == 0


async def test_non_english_submissions_are_held_without_model_calls(api):
    _, ctx, _, _ = api
    result, _ = await submit(
        api,
        report_text=(
            "J'étais en France quand un chauffeur a demandé plus d'argent que le prix "
            "convenu. Je ne savais pas quoi faire et je suis parti."
        ),
    )
    assert result.json()["status"] == "needs_review"
    ctx["llm"].complete.assert_not_called()


async def test_pii_in_location_context_cannot_create_a_public_location(api):
    from app.models import Location

    client, ctx, identity, _ = api
    result = await client.post(
        "/v1/reports",
        json={
            "report_text": BODY,
            "provenance_class": "FIRST_HAND",
            "location_text": "traveler@example.com",
            "parent_id": str(identity),
            "location_type": "area",
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert result.status_code == 202 and result.json()["status"] == "needs_review"
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(Location)) == 1
        report = await session.get(UserReport, UUID(result.json()["report_id"]))
        assert "traveler@example.com" not in json.dumps(report.processing_details)


async def test_extraction_retry_reuses_cached_relevance_and_preserves_single_graph(api):
    from app.relevance import Relevance

    _, ctx, _, _ = api
    original = ctx["llm"].complete.side_effect

    async def first_attempt(**kwargs):
        if kwargs["schema"] is Relevance:
            return await original(**kwargs)
        return Completion(error="invalid_grounding", requests=1)

    ctx["llm"].complete.side_effect = first_attempt
    _, report = await submit(api)
    with pytest.raises(Retry):
        await run_job(ctx, str(report.processing_job_id))
    ctx["llm"].complete.side_effect = original
    await run_job(ctx, str(report.processing_job_id))
    assert ctx["llm"].complete.await_count == 3
    async with ctx["sessions"]() as session:
        for model in (UserReport, Claim, Hypothesis, Evidence):
            assert await session.scalar(select(func.count()).select_from(model)) == 1
        assert (await session.get(UserReport, report.id)).processing_status == "processed"


async def test_missing_embedding_model_retains_partial_evidence_and_reports_retry(api):
    _, ctx, _, _ = api
    ctx["embedder"].preload.side_effect = OSError("fixture unavailable")
    _, report = await submit(api)
    with pytest.raises(Retry):
        await run_job(ctx, str(report.processing_job_id))
    async with ctx["sessions"]() as session:
        report = await session.get(UserReport, report.id)
        assert report.processing_status == "partial"
        assert "embedding_unavailable" in report.processing_details["error_codes"]
        assert await session.scalar(select(func.count()).select_from(Evidence)) == 1


async def test_crash_exhaustion_updates_the_report_receipt_state(api):
    _, report = await submit(api)
    _, ctx, _, _ = api
    async with ctx["sessions"]() as session, session.begin():
        job = await session.get(ResearchJob, report.processing_job_id)
        job.status, job.attempts, job.run_token = "running", 3, uuid4()
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await run_job(ctx, str(report.processing_job_id))
    async with ctx["sessions"]() as session:
        assert (await session.get(UserReport, report.id)).processing_status == "failed"
        assert (
            await session.get(ResearchJob, report.processing_job_id)
        ).error_code == "attempts_exhausted"
    ctx["llm"].complete.assert_not_called()
