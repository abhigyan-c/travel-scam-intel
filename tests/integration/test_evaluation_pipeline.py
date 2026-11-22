import json
import os

import pytest

from app.core.config import Settings
from app.integrations.sarvam import Sarvam
from benchmarks.evaluate import evaluate
from benchmarks.evaluation_metrics import (
    DATA,
    compare_baseline,
    fingerprint,
    load_corpus,
    scorecard,
)
from benchmarks.research import RecordedClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]


async def test_complete_evaluation_replays_real_adapters_without_network(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("offline evaluation attempted a provider call")

    monkeypatch.setattr(Sarvam, "complete", forbidden)
    corpus = load_corpus()
    recording = json.loads((DATA / "sarvam-recording.json").read_text(encoding="utf-8"))
    client = RecordedClient(recording["records"])
    rows = await evaluate(
        corpus, client, Settings(_env_file=None, _secrets_dir=None).database_url.get_secret_value()
    )
    baseline = json.loads((DATA / "baseline.json").read_text(encoding="utf-8"))
    assert compare_baseline(
        {
            "corpus_hash": fingerprint(corpus),
            "cases": rows,
            "scorecard": scorecard(corpus, rows),
        },
        baseline,
    )["passed"]
    assert not any(row["error"] for row in rows)
    assert 0 < client.calls <= 40 and client.live is None
    assert dict(client.positions) == {
        key: len(value) for key, value in recording["records"].items()
    }


async def test_missing_record_is_execution_failure_and_cleans_up():
    with pytest.raises(RuntimeError, match="recording_miss"):
        await evaluate(
            load_corpus(),
            RecordedClient({}),
            Settings(_env_file=None, _secrets_dir=None).database_url.get_secret_value(),
        )
