import asyncio
import os
from uuid import UUID, uuid4

import pytest
from mcp import Client
from sqlalchemy import func, select
from test_report_pipeline import BODY
from test_report_pipeline import environment as environment

from app.main import create_app
from app.models import Document, ResearchJob, UserReport

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]


async def test_form_confirmation_retries_and_unverified_links_use_existing_intake(environment):
    ctx, location, _ = environment
    application = create_app(ctx["settings"])
    application.state.db_sessions = ctx["sessions"]
    application.state.storage = ctx["storage"]
    application.state.report_queue = ctx["redis"]
    args = dict(
        report_text=BODY
        + "\n\nSubmitter-supplied source links (not checked):\nhttps://example.com/source",
        provenance_class="FIRST_HAND",
        location_id=str(location),
        idempotency_key=str(uuid4()),
        confirmed_by_user=True,
    )
    async with Client(application.state.mcp_server, mode="legacy") as client:
        form = await client.call_tool("open_scam_report_form", {"location_id": str(location)})
        assert not form.is_error and form.structured_content["location"]["id"] == str(location)
        async with ctx["sessions"]() as session:
            assert await session.scalar(select(func.count()).select_from(UserReport)) == 0
        rejected = await client.call_tool(
            "submit_scam_report", {**args, "confirmed_by_user": False}
        )
        assert rejected.is_error
        results = await asyncio.gather(
            *(client.call_tool("submit_scam_report", args) for _ in range(2))
        )
    assert all(not result.is_error for result in results)
    assert len({result.structured_content["report_id"] for result in results}) == 1
    assert {result.structured_content["replayed"] for result in results} == {True, False}
    assert all("example.com" not in result.model_dump_json() for result in results)
    ctx["llm"].complete.assert_not_called()
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(UserReport)) == 1
        assert await session.scalar(select(func.count()).select_from(ResearchJob)) == 1
        saved = await session.get(UserReport, UUID(results[0].structured_content["report_id"]))
        document = await session.get(Document, saved.document_id)
        assert "Submitter-supplied source links (not checked)" in document.clean_text
        assert document.raw_object_path.startswith("reports/")


async def test_unresolved_location_and_invalid_mixed_context(environment):
    ctx, location, _ = environment
    application = create_app(ctx["settings"])
    application.state.db_sessions = ctx["sessions"]
    application.state.storage = ctx["storage"]
    application.state.report_queue = ctx["redis"]
    args = dict(
        report_text=BODY,
        provenance_class="SECOND_HAND",
        location_text="Unknown village",
        idempotency_key=str(uuid4()),
        confirmed_by_user=True,
    )
    async with Client(application.state.mcp_server, mode="legacy") as client:
        mixed = await client.call_tool("submit_scam_report", {**args, "location_id": str(location)})
        assert mixed.is_error
        accepted = await client.call_tool("submit_scam_report", args)
        assert not accepted.is_error
    async with ctx["sessions"]() as session:
        report = await session.get(UserReport, UUID(accepted.structured_content["report_id"]))
        assert report.location_id is None
        assert report.moderation_status == "needs_review"
