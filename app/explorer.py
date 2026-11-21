"""Self-contained MCP App resource; no external assets or direct API access."""

from pathlib import Path

EXPLORER_URI = "ui://travel-scam/explorer-v1.html"
REPORTER_URI = "ui://travel-scam/report-form-v1.html"
EXPLORER_META = {
    "ui": {
        "prefersBorder": True,
        "csp": {"connectDomains": [], "resourceDomains": [], "frameDomains": []},
    }
}


def explorer_html():
    return ui_html("explorer")


def reporter_html():
    return ui_html("reporter")


def ui_html(name):
    source = Path(__file__).with_name("ui")
    script = (source / f"{name}.mjs").read_text(encoding="utf-8")
    script = script.replace(
        'import { Bridge } from "./bridge.mjs";',
        (source / "bridge.mjs").read_text(encoding="utf-8"),
    )
    return (
        (source / f"{name}.html")
        .read_text(encoding="utf-8")
        .replace("/* EXPLORER_CSS */", (source / "explorer.css").read_text(encoding="utf-8"))
        .replace("/* EXPLORER_JS */", script.replace("</script", "<\\/script"))
    )
