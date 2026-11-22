import asyncio
import io
import os
from uuid import UUID, uuid4

import httpx
import pytest
from botocore.exceptions import EndpointConnectionError
from PIL import Image, PngImagePlugin
from pydantic import SecretStr
from sqlalchemy import func, select
from test_report_pipeline import BODY, payload
from test_report_pipeline import api as api
from test_report_pipeline import environment as environment

from app.integrations.storage import create_storage_client, ensure_bucket
from app.media import MAX_BYTES
from app.models import (
    Claim,
    Evidence,
    LocationResearchState,
    ReportAttachment,
    ReportModerationEvent,
    UserReport,
)
from app.on_demand import read_location_intelligence
from app.worker import run_job

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]
ADMIN = "unit-test-admin-token-for-media-review-only"


def png(color="red"):
    output = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private", "traveler@example.invalid")
    Image.new("RGB", (16, 12), color).save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


@pytest.fixture
async def media_api(api):
    client, ctx, identity, extracted = api
    ctx["settings"].admin_api_token = SecretStr(ADMIN)
    extracted.evidence_quote = BODY
    objects = {}

    def put(**kwargs):
        objects[kwargs["Key"]] = kwargs["Body"]

    ctx["storage"].put_object.side_effect = put
    ctx["storage"].get_object.side_effect = lambda **kwargs: {
        "Body": io.BytesIO(objects[kwargs["Key"]])
    }
    key = str(uuid4())
    response = await client.post(
        "/v1/reports", json=payload(identity), headers={"Idempotency-Key": key}
    )
    assert response.status_code == 202
    report_id = UUID(response.json()["report_id"])
    return client, ctx, identity, report_id, key, objects


async def upload(
    api, *, raw=None, key=None, report_key=None, report_id=None, media_type="image/png"
):
    client, _, _, identity, original_key, _ = api
    return await client.post(
        f"/v1/reports/{report_id or identity}/attachments",
        content=png() if raw is None else raw,
        headers={
            "Idempotency-Key": key or str(uuid4()),
            "Report-Submission-Key": report_key or original_key,
            "Content-Type": media_type,
        },
    )


async def revision(api):
    client, _, _, identity, _, _ = api
    result = await client.get(
        f"/v1/admin/reports/{identity}/attachments", headers={"Authorization": f"Bearer {ADMIN}"}
    )
    assert result.status_code == 200
    return result.json()["moderation_revision"]


async def moderate(api, status, *, key=None, expected=None, token=ADMIN):
    client, _, _, identity, _, _ = api
    return await client.post(
        f"/v1/admin/reports/{identity}/moderation",
        json={
            "status": status,
            "expected_revision": await revision(api) if expected is None else expected,
            "actor": "fixture-operator",
            "reason": "Reviewed current private evidence",
        },
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key or str(uuid4())},
    )


async def process(api):
    _, ctx, _, identity, _, _ = api
    async with ctx["sessions"]() as session:
        report = await session.get(UserReport, identity)
    await run_job(ctx, str(report.processing_job_id))


async def test_slow_media_body_times_out_without_storage_or_attachment_write(media_api):
    _, ctx, _, _, _, objects = media_api
    ctx["settings"].operations.body_seconds = 0.02
    before = len(objects)

    async def slow():
        yield png()[:8]
        await asyncio.sleep(0.1)
        yield png()[8:]

    response = await upload(media_api, raw=slow())
    assert response.status_code == 408
    assert len(objects) == before
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(ReportAttachment)) == 0
    assert (await upload(media_api)).status_code == 201


async def test_private_upload_then_processing_then_authenticated_acceptance(media_api):
    result = await upload(media_api)
    assert result.status_code == 201 and result.json()["status"] == "needs_review"
    assert not any(
        word in result.text for word in ("report-media/", "traveler@", "raw_object_path")
    )
    await process(media_api)
    _, ctx, _, identity, _, objects = media_api
    async with ctx["sessions"]() as session:
        report = await session.get(UserReport, identity)
        assert report.moderation_status == report.processing_status == "needs_review"
        assert report.processing_details["text_complete"]
        evidence = await session.scalar(select(Evidence))
        assert evidence.evidence_weight == 0
        attachment = await session.scalar(select(ReportAttachment))
        assert b"traveler@example.invalid" in objects[attachment.raw_object_path]
        assert b"traveler@example.invalid" not in objects[attachment.private_object_path]
    reviewed = await moderate(media_api, "accepted")
    assert reviewed.status_code == 200 and reviewed.json()["status"] == "accepted"
    async with ctx["sessions"]() as session:
        assert (await session.scalar(select(Evidence))).evidence_weight > 0
        assert (await session.scalar(select(ReportAttachment))).status == "accepted"
        actions = set(await session.scalars(select(ReportModerationEvent.action)))
        assert actions == {"attachment_added", "processing_completed", "accepted"}


async def test_upload_after_processing_immediately_removes_public_weight(media_api):
    await process(media_api)
    _, ctx, location_id, identity, _, _ = media_api
    async with ctx["sessions"]() as session, session.begin():
        session.add(
            LocationResearchState(
                location_id=location_id, status="ready", progress={"complete": True}
            )
        )
    assert len((await read_location_intelligence(ctx, location_id)).hypotheses) == 1
    async with ctx["sessions"]() as session:
        assert (await session.scalar(select(Evidence))).evidence_weight > 0
    assert (await upload(media_api)).status_code == 201
    async with ctx["sessions"]() as session:
        assert (await session.scalar(select(Evidence))).evidence_weight == 0
        report = await session.get(UserReport, identity)
        assert report.moderation_status == "needs_review"
    assert (await read_location_intelligence(ctx, location_id)).hypotheses == []
    assert (await moderate(media_api, "accepted")).status_code == 200
    assert len((await read_location_intelligence(ctx, location_id)).hypotheses) == 1


async def test_wrong_submission_key_or_report_cannot_upload(media_api):
    for kwargs in ({"report_key": str(uuid4())}, {"report_id": uuid4()}):
        assert (await upload(media_api, **kwargs)).status_code == 404
    _, ctx, _, _, _, _ = media_api
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(ReportAttachment)) == 0


async def test_uploads_are_idempotent_and_body_conflicts_do_not_replace_files(media_api):
    key = str(uuid4())
    one = await upload(media_api, key=key)
    two = await upload(media_api, key=key)
    assert one.json()["attachment_id"] == two.json()["attachment_id"]
    assert two.json()["replayed"]
    assert (await upload(media_api, key=key, raw=png("blue"))).status_code == 409
    _, ctx, _, _, _, _ = media_api
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(ReportAttachment)) == 1
        assert await session.scalar(select(func.count()).select_from(ReportModerationEvent)) == 1


async def test_concurrent_uploads_cannot_exceed_the_per_report_limit(media_api):
    results = await asyncio.gather(
        *(upload(media_api, raw=png(color)) for color in ("red", "blue", "green", "black", "white"))
    )
    assert sorted(result.status_code for result in results) == [201, 201, 201, 201, 409]
    _, ctx, _, _, _, _ = media_api
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(ReportAttachment)) == 4


@pytest.mark.parametrize(
    "body,mime,status",
    [
        (b"bad", "image/png", 422),
        (b"<svg/>", "image/svg+xml", 415),
        (b"x" * (MAX_BYTES + 1), "image/png", 413),
    ],
    ids=["corrupt", "unsupported", "oversized"],
)
async def test_invalid_media_does_not_create_records_or_moderation_holds(
    media_api, body, mime, status
):
    assert (await upload(media_api, raw=body, media_type=mime)).status_code == status
    _, ctx, _, identity, _, _ = media_api
    async with ctx["sessions"]() as session:
        assert (await session.get(UserReport, identity)).moderation_status == "pending"
        assert await session.scalar(select(func.count()).select_from(ReportAttachment)) == 0


async def test_private_download_requires_admin_and_is_metadata_free(media_api):
    result = await upload(media_api)
    client, _, _, identity, _, _ = media_api
    path = f"/v1/admin/reports/{identity}/attachments/{result.json()['attachment_id']}"
    assert (await client.get(path)).status_code == 401
    response = await client.get(path, headers={"Authorization": f"Bearer {ADMIN}"})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert b"traveler@example.invalid" not in response.content
    assert (
        await client.get(f"/v1/reports/{identity}/attachments/{result.json()['attachment_id']}")
    ).status_code == 404


async def test_admin_is_disabled_without_configuration_and_bad_tokens_are_not_echoed(media_api):
    client, ctx, _, identity, _, _ = media_api
    assert (
        await moderate(media_api, "rejected", expected=0, token="wrong-secret")
    ).status_code == 401
    ctx["settings"].admin_api_token = SecretStr("")
    result = await client.get(
        f"/v1/admin/reports/{identity}/attachments",
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert result.status_code == 503 and "wrong-secret" not in result.text


async def test_review_revision_and_idempotency_prevent_releasing_unseen_new_uploads(media_api):
    await process(media_api)
    await upload(media_api)
    original_revision, key = await revision(media_api), str(uuid4())
    accepted = await moderate(media_api, "accepted", expected=original_revision, key=key)
    assert accepted.status_code == 200
    await upload(media_api, raw=png("blue"))
    replayed = await moderate(media_api, "accepted", expected=original_revision, key=key)
    assert replayed.json()["replayed"] and replayed.json()["status"] == "needs_review"
    stale = await moderate(media_api, "accepted", expected=original_revision)
    assert stale.status_code == 409
    conflict = await moderate(media_api, "rejected", expected=original_revision, key=key)
    assert conflict.status_code == 409


async def test_sensitive_text_cannot_be_released_even_with_an_admin_token(media_api):
    _, ctx, _, _, _, _ = media_api
    # Use the controlled model's normal ordinary extraction, then simulate a newly imposed hold.
    await process(media_api)
    async with ctx["sessions"]() as session, session.begin():
        claim = await session.scalar(select(Claim))
        claim.structured_data = {
            **claim.structured_data,
            "sensitive": True,
            "entities": ["Named actor"],
            "publication_status": "held",
        }
    assert (await upload(media_api)).status_code == 201
    denied = await moderate(media_api, "accepted")
    assert denied.status_code == 409
    async with ctx["sessions"]() as session:
        assert (await session.scalar(select(Evidence))).evidence_weight == 0


async def test_rejection_hides_evidence_and_rejected_report_does_not_accept_new_files(media_api):
    await process(media_api)
    await upload(media_api)
    rejected = await moderate(media_api, "rejected")
    assert rejected.status_code == 200
    assert (await upload(media_api, raw=png("blue"))).status_code == 409
    _, ctx, location_id, _, _, _ = media_api
    async with ctx["sessions"]() as session:
        assert (await session.scalar(select(Evidence))).evidence_weight == 0
    assert (await read_location_intelligence(ctx, location_id)).hypotheses == []


async def test_corrupt_private_object_is_not_served(media_api):
    result = await upload(media_api)
    client, ctx, _, identity, _, objects = media_api
    async with ctx["sessions"]() as session:
        attachment = await session.get(ReportAttachment, UUID(result.json()["attachment_id"]))
        objects[attachment.private_object_path] = b"corrupt"
    response = await client.get(
        f"/v1/admin/reports/{identity}/attachments/{attachment.id}",
        headers={"Authorization": f"Bearer {ADMIN}"},
    )
    assert response.status_code == 503


async def test_failed_storage_rolls_back_upload_hold_and_retry_is_safe(media_api):
    _, ctx, _, identity, _, objects = media_api
    original_put = ctx["storage"].put_object.side_effect

    def put(**kwargs):
        if "/normalized/" in kwargs["Key"]:
            raise EndpointConnectionError(endpoint_url="fixture")
        return original_put(**kwargs)

    ctx["storage"].put_object.side_effect = put
    key = str(uuid4())
    assert (await upload(media_api, key=key)).status_code == 503
    async with ctx["sessions"]() as session:
        assert (await session.get(UserReport, identity)).moderation_status == "pending"
        assert await session.scalar(select(func.count()).select_from(ReportAttachment)) == 0
    ctx["storage"].put_object.side_effect = original_put
    assert (await upload(media_api, key=key)).status_code == 201


async def test_incomplete_text_cannot_be_marked_accepted(media_api):
    assert (await upload(media_api)).status_code == 201
    response = await moderate(media_api, "accepted")
    assert response.status_code == 409


async def test_real_object_storage_keeps_media_private_and_admin_preview_verifies_hash(media_api):
    client, ctx, _, identity, _, _ = media_api
    storage = create_storage_client(ctx["settings"])
    ensure_bucket(storage, ctx["bucket"])
    ctx["storage"] = storage
    client._transport.app.state.storage = storage
    keys = []
    try:
        result = await upload(media_api)
        assert result.status_code == 201
        async with ctx["sessions"]() as session:
            attachment = await session.get(ReportAttachment, UUID(result.json()["attachment_id"]))
            keys = [attachment.raw_object_path, attachment.private_object_path]
        async with httpx.AsyncClient(trust_env=False) as anonymous:
            public = await anonymous.get(
                f"{ctx['settings'].s3_endpoint}/{ctx['bucket']}/{attachment.private_object_path}"
            )
            assert public.status_code == 403
        review = await client.get(
            f"/v1/admin/reports/{identity}/attachments/{attachment.id}",
            headers={"Authorization": f"Bearer {ADMIN}"},
        )
        assert review.status_code == 200
        assert b"traveler@example.invalid" not in review.content
    finally:
        for key in keys:
            storage.delete_object(Bucket=ctx["bucket"], Key=key)
        storage.close()
