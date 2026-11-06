from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.integrations.sarvam import Completion
from app.relevance import Relevance
from benchmarks.research import RecordedClient, load_corpus, scorecard
from benchmarks.snapshot_research import safe_job


async def test_recorded_client_revalidates_and_never_falls_back_to_network():
    records = {}
    live = AsyncMock()
    live.complete.return_value = Completion(
        data=Relevance(relevant=False, category=None, confidence=0.9, reason="Ordinary tourism")
    )
    request = dict(system="fixture", user="fixture text", schema=Relevance)
    capture = RecordedClient(records, live=live, max_calls=1)
    expected = await capture.complete(**request)
    replay = RecordedClient(records)
    assert (await replay.complete(**request)).data == expected.data
    with pytest.raises(RuntimeError, match="recording_miss"):
        await replay.complete(**{**request, "user": "changed input"})
    with pytest.raises(RuntimeError, match="live_call_limit"):
        await capture.complete(**request)
    assert live.complete.await_count == 1


def test_corpus_and_targets_are_not_vacuous():
    corpus = load_corpus()
    assert len(corpus["cases"]) >= 12
    assert {c["split"] for c in corpus["cases"]} == {"development", "holdout"}
    assert len({c["city"] for c in corpus["cases"]}) >= 5
    assert not any(m["meets_target"] for cache in scorecard([]).values() for m in cache.values())


def test_failed_acquisition_cannot_count_as_a_positive_claim():
    row = {
        "cache": "cold",
        "expected": {"claim": True, "location_path": ["Fiji", "Suva"], "public_pattern": True},
        "grounded_claim_check": False,
        "geography_check": False,
        "public_check": False,
        "claims": 0,
        "public_patterns": 0,
    }
    metrics = scorecard([row])["cold"]
    assert metrics["positive_claim_coverage"]["passed"] == 0
    assert not metrics["eligible_pattern_coverage"]["meets_target"]


def test_production_snapshot_omits_source_content_and_preserves_missing_telemetry():
    now = datetime.now(UTC)
    job = SimpleNamespace(
        payload={"phase": "initial"},
        result={
            "status": "partial",
            "complete": False,
            "secret": "never-export",
            "documents": [
                {
                    "url": "private-url",
                    "extraction_attempts": [
                        {"error": "invalid_grounding", "data": "private-quote"}
                    ],
                }
            ],
        },
        status="succeeded",
        created_at=now,
        updated_at=now,
        error_code="research_incomplete",
    )
    snapshot = safe_job("Bangkok", job)
    assert snapshot["claims"] is None and snapshot["llm_calls"] is None
    assert snapshot["extraction_errors"] == {"invalid_grounding": 1}
    assert "private" not in str(snapshot) and "never-export" not in str(snapshot)
