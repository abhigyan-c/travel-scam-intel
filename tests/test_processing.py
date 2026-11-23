import pytest

from app.processing import process_bytes

ENGLISH = (
    "Travelers reported that the driver asked for more money after the journey. "
    "They had agreed to a fixed fare before leaving the station. "
    "The visitors refused the extra charge and asked for the original agreement to be respected. "
    "This fixture tests article processing and does not describe a real incident."
)


def article(text=ENGLISH, title="Travel report"):
    return f"""<!doctype html><html lang="fr"><head><title>{title}</title>
    <meta name="author" content="Fixture Writer">
    <meta property="article:published_time" content="2026-01-02T10:00:00Z">
    <link rel="canonical" href="https://example.org/report"></head><body>
    <nav>Home Contact Subscribe</nav><script>ignore this script</script>
    <article><h1>{title}</h1><p>{text}</p><p>{text}</p></article>
    <footer>All rights reserved</footer></body></html>""".encode()


def test_article_text_and_metadata():
    result = process_bytes(article(), content_type="text/html", url="https://example.org/raw")
    assert result.outcome == "ready" and result.language == "en"
    assert "fixed fare" in result.text
    assert "ignore this script" not in result.text and "Home Contact Subscribe" not in result.text
    assert result.metadata["title"] == "Travel report"
    assert result.metadata["author"] == "Fixture Writer"
    assert result.metadata["date"].startswith("2026-01-02")
    assert result.metadata["canonical_url"] == "https://example.org/report"
    assert len(result.metadata["clean_text_hash"]) == 64
    assert process_bytes(article(), content_type="text/html").language == result.language


def test_non_english_retained_and_declared_language_not_trusted():
    text = (
        "Les voyageurs ont signalé que le chauffeur demandait davantage d'argent après le voyage. "
        "Ils avaient convenu d'un prix fixe avant de quitter la gare. " * 4
    )
    result = process_bytes(text.encode(), content_type="text/plain")
    assert result.outcome == "non_english" and result.language == "fr" and result.text


@pytest.mark.parametrize(
    "raw,content_type,outcome",
    [
        (b"", "text/plain", "empty"),
        (b"tiny", "text/plain", "unknown_language"),
        (b"1234", "text/plain", "unknown_language"),
        (b"invalid\xff", "text/plain", "invalid_encoding"),
        (b"text", "application/pdf", "unsupported_content_type"),
        (b"x" * 2_000_001, "text/plain", "too_large"),
        (b"<html><body><script>hidden</script></body></html>", "text/html", "empty"),
    ],
    ids=["empty", "short", "numbers", "encoding", "unsupported", "oversize", "scripts-only"],
)
def test_unusable_content_is_explicit(raw, content_type, outcome):
    assert process_bytes(raw, content_type=content_type).outcome == outcome


def test_declared_text_charset_and_blocked_page():
    result = process_bytes(
        ENGLISH.encode("cp1252"), content_type="text/plain; charset=windows-1252"
    )
    assert result.language == "en"
    blocked = process_bytes(article(title="Access Denied"), content_type="text/html")
    assert blocked.outcome == "blocked" and blocked.text is None
