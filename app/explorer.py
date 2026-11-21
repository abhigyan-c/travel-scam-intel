"""Self-contained MCP App resource; no external assets or direct API access."""

from pathlib import Path

EXPLORER_URI = "ui://travel-scam/explorer-v1.html"
EXPLORER_META = {
    "ui": {
        "prefersBorder": True,
        "csp": {"connectDomains": [], "resourceDomains": [], "frameDomains": []},
    }
}


def explorer_html():
    source = Path(__file__).with_name("ui")
    return (
        (source / "explorer.html")
        .read_text(encoding="utf-8")
        .replace("/* EXPLORER_CSS */", (source / "explorer.css").read_text(encoding="utf-8"))
        .replace("/* EXPLORER_JS */", (source / "explorer.mjs").read_text(encoding="utf-8"))
    )
