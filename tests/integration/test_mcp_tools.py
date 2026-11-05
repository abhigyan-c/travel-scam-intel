import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from mcp import Client
from sqlalchemy import func, select
from test_on_demand_research import environment as environment
from test_on_demand_research import finish_enrichment

from app.main import create_app
from app.models import Document, Location, LocationResearchState, ResearchJob, UserReport
from app.on_demand import get_location_intelligence

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]


@pytest.fixture
async def mcp_environment(environment):
    ctx, location_id, claim = environment
    application = create_app(ctx["settings"])
    application.state.db_sessions = ctx["sessions"]
    application.state.storage = ctx["storage"]
    application.state.report_queue = ctx["redis"]
    application.state.embedder = ctx["embedder"]

    # pytest-asyncio runs async fixture setup/teardown in separate tasks. Keep
    # AnyIO cancellation scopes inside the individual call's task instead.
    class Calls:
        async def call_tool(self, name, arguments):
            async with Client(application.state.mcp_server, mode="legacy") as client:
                return await client.call_tool(name, arguments)

    return Calls(), ctx, location_id, claim


async def test_unseen_search_queues_once_and_poll_does_not_requeue(mcp_environment):
    client, ctx, location_id, _ = mcp_environment
    initial = await client.call_tool("search_scam_patterns", {"location_id": str(location_id)})
    again = await client.call_tool("search_scam_patterns", {"location_id": str(location_id)})
    poll = await client.call_tool(
        "search_scam_patterns",
        {
            "location_id": str(location_id),
            "request_research": False,
        },
    )
    for result in [initial, again, poll]:
        assert not result.is_error
        assert result.structured_content["outcome"] == "research_incomplete"
        assert result.structured_content["research"]["in_progress"] is True
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(ResearchJob)) == 1
    ctx["provider"].search.assert_not_called()
    ctx["llm"].complete.assert_not_called()


async def test_fresh_search_reuses_scored_public_evidence_and_stale_queues(mcp_environment):
    client, ctx, location_id, _ = mcp_environment
    await get_location_intelligence(ctx, location_id)
    await finish_enrichment(ctx, location_id)
    async with ctx["sessions"]() as session, session.begin():
        state = await session.get(LocationResearchState, location_id)
        state.fresh_until = datetime.now(UTC) + timedelta(hours=1)
    result = await client.call_tool("search_scam_patterns", {"location_id": str(location_id)})
    assert not result.is_error and len(result.structured_content["items"]) == 1
    card = result.structured_content["items"][0]
    evidence = await client.call_tool("get_scam_evidence", {"pattern_id": card["id"]})
    assert not evidence.is_error
    assert evidence.structured_content["items"][0]["label"] == "public_web_source"
    detail = await client.call_tool("get_scam_pattern", {"pattern_id": card["id"]})
    assert detail.structured_content["id"] == card["id"]
    async with ctx["sessions"]() as session, session.begin():
        state = await session.get(LocationResearchState, location_id)
        state.fresh_until = datetime.now(UTC) - timedelta(seconds=1)
        before = await session.scalar(select(func.count()).select_from(ResearchJob))
    stale = await client.call_tool("search_scam_patterns", {"location_id": str(location_id)})
    assert stale.structured_content["research"]["in_progress"]
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(ResearchJob)) == before + 1


async def test_location_clarification_and_provisional_creation(mcp_environment):
    client, ctx, root, _ = mcp_environment
    missing = await client.call_tool("resolve_scam_location", {"location": "Suva"})
    assert missing.structured_content["status"] == "unresolved"
    arguments = {"location": "Suva", "parent_id": str(root), "location_type": "city"}
    first = await client.call_tool("resolve_scam_location", arguments)
    second = await client.call_tool("resolve_scam_location", arguments)
    assert first.structured_content["status"] == "provisional"
    assert first.structured_content["location_id"] == second.structured_content["location_id"]
    async with ctx["sessions"]() as session, session.begin():
        session.add_all(
            [
                Location(
                    name="Springfield", canonical_name="springfield", type="city", parent_id=root
                ),
                Location(name="Springfield", canonical_name="springfield", type="city"),
            ]
        )
    ambiguous = await client.call_tool("resolve_scam_location", {"location": "Springfield"})
    assert ambiguous.structured_content["status"] == "ambiguous"
    assert "location_id" not in ambiguous.structured_content


async def test_report_submission_is_confirmed_idempotent_and_normalized(mcp_environment):
    client, ctx, location_id, _ = mcp_environment
    arguments = {
        "report_text": "During my trip a driver demanded extra money after I agreed a fixed fare.",
        "provenance_class": "FIRST_HAND",
        "idempotency_key": str(uuid4()),
        "location_id": str(location_id),
        "confirmed_by_user": True,
    }
    rejected = await client.call_tool(
        "submit_scam_report", {**arguments, "confirmed_by_user": False}
    )
    assert rejected.is_error
    first = await client.call_tool("submit_scam_report", arguments)
    second = await client.call_tool("submit_scam_report", arguments)
    assert not first.is_error and not second.is_error
    assert first.structured_content["report_id"] == second.structured_content["report_id"]
    assert second.structured_content["replayed"] is True
    conflict = await client.call_tool(
        "submit_scam_report",
        {
            **arguments,
            "report_text": arguments["report_text"] + " Changed details.",
        },
    )
    assert conflict.is_error and conflict.structured_content["error"]["code"] == "conflict"
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(UserReport)) == 1
        report = await session.get(UserReport, UUID(first.structured_content["report_id"]))
        document = await session.get(Document, report.document_id)
        assert document.processing_status == "processed" and document.clean_text
    assert arguments["report_text"] not in first.model_dump_json()
    ctx["llm"].complete.assert_not_called()
