from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.db.claim_pipeline import apply_and_score_claim
from app.db.research_runs import reasons
from app.services.research_reports import freshness


def test_freshness_boundary_and_unknown_times_are_not_completion_flags():
    now = datetime(2026, 11, 20, tzinfo=UTC)
    assert freshness([now.isoformat()], now, 60)[0] == "fresh"
    assert freshness([now.isoformat()], now + timedelta(seconds=60), 60)[0] == "stale"
    assert freshness([None], now, 60) == ("unknown", None)
    assert freshness([None, now.isoformat()], now, 60)[0] == "unknown"
    assert freshness([(now - timedelta(seconds=61)).isoformat(), None], now, 60)[0] == "stale"


def test_generic_partial_or_combined_limit_does_not_fabricate_precise_cause():
    assert reasons({"complete": False}) == ["processing_incomplete"]
    assert reasons({"errors": [{"code": "resource_limit_or_deadline"}]}) == [
        "processing_incomplete"
    ]
    assert reasons({"error_code": "deadline"}) == ["time_limit"]


async def test_denied_comparator_budget_is_not_reported_as_provider_unavailability(monkeypatch):
    import app.db.claim_pipeline as pipeline

    session = AsyncMock()
    session.get.return_value = SimpleNamespace(location_id=None)
    resolution = SimpleNamespace(
        id=uuid4(),
        error="semantic_provider_failed",
        needs_review=True,
        details={"completion": {"error": "run_llm_limit", "requests": 0}},
    )
    monkeypatch.setattr(pipeline, "resolve_hypothesis", AsyncMock(return_value=resolution))
    value = await apply_and_score_claim(session, uuid4(), embedder=None, client=None)
    assert value["error"] == "resource_limit"
    assert resolution.error == "semantic_provider_failed"
