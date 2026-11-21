from mcp import Client

from app.core.config import Settings
from app.explorer import EXPLORER_URI, explorer_html
from app.main import create_app


async def test_ui_resource_and_attachment_only_on_initial_request():
    application = create_app(Settings(_env_file=None, environment="test"))
    async with Client(application.state.mcp_server, mode="legacy") as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        resources = await client.list_resources()
        result = await client.read_resource(EXPLORER_URI)
    assert [str(resource.uri) for resource in resources.resources] == [EXPLORER_URI]
    assert tools["request_scam_report"].meta["ui"]["resourceUri"] == EXPLORER_URI
    assert tools["watch_scam_report"].meta["ui"]["visibility"] == ["app"]
    assert tools["watch_scam_report"].annotations.open_world_hint is False
    assert tools["watch_scam_report"].annotations.idempotent_hint is False
    assert not any(
        "resourceUri" in (tool.meta or {}).get("ui", {})
        for name, tool in tools.items()
        if name != "request_scam_report"
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
