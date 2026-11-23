from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.intelligence import PublicMetrics, ResearchState
from app.services.public_evidence import public_text, public_url


def test_unknown_metrics_are_explicit_and_contract_rejects_internal_fields():
    metrics = PublicMetrics(status="unavailable", as_of=datetime.now(UTC))
    assert metrics.confidence_score is None and metrics.confidence_label == "unknown"
    assert not metrics.complete and metrics.all_time is None
    with pytest.raises(ValidationError):
        ResearchState(status="never_researched", run_token="private")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/post?token=private",
        "http://127.0.0.1/post",
        "http://localhost/post",
        "javascript:alert(1)",
        "https://user:secret@example.org/post",
    ],
)
def test_private_or_unsafe_source_links_are_omitted(url):
    assert public_url(url) is None


def test_public_excerpt_redaction_and_length():
    assert "person@example.org" not in public_text("Email person@example.org for information", 500)
    assert public_text("I will hurt the driver", 500) == "[Content withheld]"
    assert len(public_text("a" * 800, 500)) == 500
    assert public_url("https://example.org/post#discussion") == "https://example.org/post"
