from mcp import Client

from app.core.config import Settings
from app.explorer import EXPLORER_URI, REPORTER_URI, explorer_html, reporter_html
from app.main import create_app


async def test_ui_resource_and_attachment_only_on_initial_request():
    application = create_app(Settings(_env_file=None, environment="test"))
    async with Client(application.state.mcp_server, mode="legacy") as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        resources = await client.list_resources()
        result = await client.read_resource(EXPLORER_URI)
    assert {str(resource.uri) for resource in resources.resources} == {EXPLORER_URI, REPORTER_URI}
    assert tools["request_scam_report"].meta["ui"]["resourceUri"] == EXPLORER_URI
    assert tools["watch_scam_report"].meta["ui"]["visibility"] == ["app"]
    assert tools["watch_scam_report"].annotations.open_world_hint is False
    assert tools["watch_scam_report"].annotations.idempotent_hint is False
    assert not any(
        "resourceUri" in (tool.meta or {}).get("ui", {})
        for name, tool in tools.items()
        if name not in {"request_scam_report", "open_scam_report_form"}
    )
    content = result.contents[0]
    assert content.mime_type == "text/html;profile=mcp-app"
    assert content.meta["ui"]["csp"] == {
        "connectDomains": [],
        "resourceDomains": [],
        "frameDomains": [],
    }
    assert "/* EXPLORER_" not in content.text
    assert len(content.text.encode()) < 50_000


def test_view_cannot_fetch_submit_or_inject_source_markup():
    html = explorer_html()
    assert "fetch(" not in html and "XMLHttpRequest" not in html
    assert "submit_scam_report" not in html and '"request_scam_report"' not in html
    assert "innerHTML" not in html and "ui/message" not in html
    assert "textContent" in html and "event.source !== win.parent" in html
    assert "<script src=" not in html and "<img" not in html


async def test_report_form_has_no_draft_in_tool_result_or_storage():
    application = create_app(Settings(_env_file=None, environment="test"))
    async with Client(application.state.mcp_server, mode="legacy") as client:
        result = await client.call_tool("open_scam_report_form", {})
        resource = await client.read_resource(REPORTER_URI)
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert not result.is_error
    assert set(result.structured_content) == {"contract_version", "form_id", "location"}
    assert result.structured_content["location"] is None
    assert tools["open_scam_report_form"].annotations.read_only_hint
    assert tools["open_scam_report_form"].meta["ui"]["resourceUri"] == REPORTER_URI
    html = resource.contents[0].text
    assert "/* EXPLORER_" not in html and 'from "./bridge.mjs"' not in html
    assert html == reporter_html()
    assert all(
        word not in html
        for word in [
            "innerHTML",
            "localStorage",
            "sessionStorage",
            "ui/message",
            "ui/update-model-context",
            "fetch(",
        ]
    )
