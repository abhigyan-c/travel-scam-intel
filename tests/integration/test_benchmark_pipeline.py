import json
import os
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.db.session import create_database_engine
from app.extraction import ClaimBatch, ExtractedClaim
from app.integrations.sarvam import Completion
from app.relevance import Relevance
from benchmarks.research import (
    CURRENT_RECORDING,
    RecordedClient,
    isolated_database,
    load_corpus,
    run_case,
)

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
    engine = create_database_engine(Settings())
    async with engine.connect() as connection:
        before = (
            await connection.execute(
                text(
                    "SELECT (SELECT count(*) FROM public.claims), "
                    "(SELECT count(*) FROM public.locations), "
                    "(SELECT count(*) FROM pg_namespace WHERE nspname LIKE 'benchmark_%')"
                )
            )
        ).one()
    captured = await run_case(case, RecordedClient(records, live=live), database_url)
    replayed = await run_case(case, RecordedClient(records), database_url)
    for rows in (captured, replayed):
        cold, warm = rows
        assert cold["claims"] == warm["claims"] == 1
        assert cold["public_patterns"] == warm["public_patterns"] == 1
        assert cold["geography_check"] and cold["lineage_check"]
        assert cold["llm_calls"] == 2 and warm["llm_calls"] == 0
    assert live.complete.await_count == 2
    try:
        async with engine.connect() as connection:
            after = (
                await connection.execute(
                    text(
                        "SELECT (SELECT count(*) FROM public.claims), "
                        "(SELECT count(*) FROM public.locations), "
                        "(SELECT count(*) FROM pg_namespace WHERE nspname LIKE 'benchmark_%')"
                    )
                )
            ).one()
        assert after == before
    finally:
        await engine.dispose()


async def test_committed_recordings_run_all_cases_without_live_services():
    corpus = load_corpus()
    recording = json.loads(CURRENT_RECORDING.read_text())
    client = RecordedClient(recording["records"])
    for case in corpus["cases"]:
        cold, warm = await run_case(case, client, Settings().database_url.get_secret_value())
        assert cold["lineage_check"] and warm["lineage_check"]
        assert warm["claims"] >= cold["claims"]
        if cold["incomplete_control_check"] is not None:
            assert cold["incomplete_control_check"] and warm["incomplete_control_check"]
    assert client.calls <= 40


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
