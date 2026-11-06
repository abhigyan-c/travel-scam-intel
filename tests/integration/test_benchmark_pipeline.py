import os
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.extraction import ClaimBatch, ExtractedClaim
from app.integrations.sarvam import Completion
from app.relevance import Relevance
from benchmarks.research import RecordedClient, isolated_database, load_corpus, run_case

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]


async def test_actual_pipeline_replays_cold_and_warm_without_changing_public_schema():
    case = load_corpus()["cases"][0]
    live = AsyncMock()

    async def complete(**kwargs):
        data = Relevance(relevant=True, category="fare", confidence=0.9, reason="Fare demand")
        if kwargs["schema"] is not Relevance:
            data = ClaimBatch(
                claims=[
                    ExtractedClaim(
                        claim="A driver demanded extra money after agreeing to a fixed fare.",
                        evidence_quote=case["text"],
                        location="Bangkok",
                        approach=[],
                        methods=["demanded extra money"],
                        warning_signs=[],
                        entities=[],
                        occurred_at=None,
                        first_hand=True,
                        confidence=0.9,
                        sensitive=False,
                    )
                ]
            )
        return Completion(data=data, requests=1, usage={"total_tokens": 10})

    live.complete.side_effect = complete
    records = {}
    database_url = Settings().database_url.get_secret_value()
    captured = await run_case(case, RecordedClient(records, live=live), database_url)
    replayed = await run_case(case, RecordedClient(records), database_url)
    for rows in (captured, replayed):
        cold, warm = rows
        assert cold["claims"] == warm["claims"] == 1
        assert cold["public_patterns"] == warm["public_patterns"] == 1
        assert cold["geography_check"] and cold["lineage_check"]
        assert cold["llm_calls"] == 2 and warm["llm_calls"] == 0
    assert live.complete.await_count == 2


async def test_remote_database_is_rejected_before_connecting():
    with pytest.raises(ValueError, match="requires_local_database"):
        async with isolated_database("postgresql+psycopg://user:secret@production.example/db"):
            pytest.fail("remote database opened")


async def test_recording_miss_is_an_execution_error_not_a_quality_score():
    with pytest.raises(RuntimeError, match="recording_miss"):
        await run_case(
            load_corpus()["cases"][0],
            RecordedClient({}),
            Settings().database_url.get_secret_value(),
        )
