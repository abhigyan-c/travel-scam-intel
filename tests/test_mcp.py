import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from mcp import Client

from app import mcp_server
from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def application():
    return create_app(Settings(_env_file=None, environment="test"))


async def test_tool_discovery_and_accurate_annotations(application):
    async with Client(application.state.mcp_server, mode="legacy") as client:
        result = await client.list_tools()
    tools = {tool.name: tool for tool in result.tools}
    assert set(tools) == {
        "resolve_scam_location",
        "search_scam_patterns",
        "get_scam_pattern",
        "get_scam_evidence",
        "match_possible_scam",
        "get_recent_patterns",
        "submit_scam_report",
        "request_scam_report",
        "get_scam_report",
        "wait_for_scam_report",
        "watch_scam_report",
        "open_scam_report_form",
    }
    assert tools["get_scam_evidence"].annotations.read_only_hint is True
    assert tools["search_scam_patterns"].annotations.read_only_hint is False
    assert tools["search_scam_patterns"].annotations.open_world_hint is True
    assert tools["submit_scam_report"].annotations.idempotent_hint is True
    assert tools["get_scam_evidence"].input_schema["properties"]["limit"]["maximum"] == 20


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("get_scam_pattern", {"pattern_id": "private@example.com"}),
        ("get_scam_evidence", {"pattern_id": str(uuid4()), "limit": 1000}),
        (
            "submit_scam_report",
            {
                "report_text": "A private report by private@example.com.",
                "provenance_class": "FIRST_HAND",
                "idempotency_key": str(uuid4()),
                "confirmed_by_user": False,
            },
        ),
        ("private@example.com", {}),
    ],
)
async def test_invalid_inputs_are_errors_without_reflecting_values(application, name, arguments):
    async with Client(application.state.mcp_server, mode="legacy") as client:
        result = await client.call_tool(name, arguments)
    assert result.is_error
    assert "private@example.com" not in result.model_dump_json()
    assert result.structured_content["error"]["code"] == "invalid_request"


async def test_search_reuses_service_and_durable_background_request(application, monkeypatch):
    application.state.db_sessions = object()
    application.state.report_queue = object()
    location_id = uuid4()
    payload = {"items": [], "outcome": "research_incomplete", "research": {"in_progress": True}}
    service = SimpleNamespace(
        search_location_scams=AsyncMock(
            return_value=SimpleNamespace(model_dump=lambda **kwargs: payload)
        )
    )
    monkeypatch.setattr(mcp_server, "IntelligenceService", lambda *args, **kwargs: service)
    request = AsyncMock(return_value=True)
    monkeypatch.setattr(mcp_server, "request_location_research", request)
    async with Client(application.state.mcp_server, mode="legacy") as client:
        initial = await client.call_tool("search_scam_patterns", {"location_id": str(location_id)})
        poll = await client.call_tool(
            "search_scam_patterns",
            {
                "location_id": str(location_id),
                "request_research": False,
            },
        )
    assert not initial.is_error and initial.structured_content["queue_pending"] is True
    assert poll.structured_content["research"]["in_progress"] is True
    assert poll.structured_content["outcome"] == "research_incomplete"
    assert request.await_count == 1
    assert request.call_args.args[1] == location_id
    assert service.search_location_scams.await_count == 2


async def test_storage_errors_are_sanitized(application, monkeypatch):
    application.state.db_sessions = object()
    service = SimpleNamespace(get_hypothesis=AsyncMock(side_effect=RuntimeError("private-token")))
    monkeypatch.setattr(mcp_server, "IntelligenceService", lambda *args, **kwargs: service)
    async with Client(application.state.mcp_server, mode="legacy") as client:
        result = await client.call_tool("get_scam_pattern", {"pattern_id": str(uuid4())})
    assert result.is_error and result.structured_content["error"]["code"] == "unavailable"
    assert "private-token" not in result.model_dump_json()


async def test_streamable_http_no_redirect_and_host_origin_protection(application):
    headers = {"Accept": "application/json, text/event-stream"}
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1"},
        },
    }
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://localhost"
        ) as client:
            response = await client.post("/mcp", json=initialize, headers=headers)
            assert response.status_code == 200
            assert response.headers["X-Request-ID"]
            assert response.json()["result"]["protocolVersion"] == "2025-11-25"
            bad_host = await client.post(
                "/mcp", json=initialize, headers={**headers, "Host": "evil.test"}
            )
            assert bad_host.status_code == 421
            bad_origin = await client.post(
                "/mcp", json=initialize, headers={**headers, "Origin": "https://evil.test"}
            )
            assert bad_origin.status_code == 403
            invalid = await client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "get_scam_pattern", "arguments": {"pattern_id": "secret"}},
                },
            )
            assert invalid.json()["result"]["isError"] is True
            assert (
                invalid.json()["result"]["structuredContent"]["error"]["request_id"]
                == invalid.headers["X-Request-ID"]
            )
            assert "secret" not in json.dumps(invalid.json())
            missing = await client.get("/missing")
            assert missing.json()["error"]["code"] == "not_found"


def test_blank_mcp_public_url_means_local_only():
    assert Settings(_env_file=None, mcp_public_url="").mcp_public_url is None


def test_sdk_warnings_cannot_fall_back_to_stderr(application, capsys):
    child = logging.getLogger("mcp.test_sensitive_warning")
    previous = child.level
    try:
        child.setLevel(logging.WARNING)
        child.warning("test-private-report-body")
        assert "test-private-report-body" not in capsys.readouterr().err
    finally:
        child.setLevel(previous)


async def test_public_hostname_alias_is_allowed_without_disabling_rebinding_checks():
    application = create_app(
        Settings(
            _env_file=None,
            environment="test",
            mcp_public_url="https://192.0.2.1/mcp",
            mcp_public_aliases=["https://preview.example.test/mcp"],
        )
    )
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream"}
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="https://preview.example.test"
        ) as client:
            allowed = await client.post(
                "/mcp",
                json=initialize,
                headers={
                    **headers,
                    "Origin": "https://preview.example.test",
                },
            )
            assert allowed.status_code == 200
            blocked = await client.post(
                "/mcp",
                json=initialize,
                headers={
                    **headers,
                    "Host": "unapproved.example.test",
                },
            )
            assert blocked.status_code == 421
