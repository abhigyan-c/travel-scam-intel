import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.db.jobs import validate_payload
from app.integrations.sarvam import Completion
from app.models.research_job import JobKind
from app.on_demand import limits_for, search_label
from app.research import _LimitedClient


def test_interactive_and_enrichment_limits_are_bounded():
    settings = Settings(_env_file=None)
    fast, deep = limits_for(settings, "initial"), limits_for(settings, "enrichment")
    assert (fast.queries, fast.candidates, fast.fetches, fast.documents, fast.timeout) == (
        3,
        30,
        10,
        6,
        25,
    )
    assert deep.timeout == 45 and deep.fetches == 20 and deep.llm_calls == 24
    assert deep.llm_concurrency == fast.llm_concurrency == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"research_max_total_seconds": 46},
        {"research_max_search_queries": 7},
        {"research_max_llm_concurrency": 0},
        {"research_fresh_seconds": 0},
    ],
)
def test_invalid_research_configuration_is_rejected(changes):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **changes)


def test_location_jobs_validate_phase_and_identity():
    identity = str(uuid4())
    assert validate_payload(
        JobKind.LOCATION_RESEARCH, {"location_id": identity, "phase": "initial"}
    ) == {"location_id": identity, "phase": "initial"}
    with pytest.raises(ValueError):
        validate_payload(JobKind.LOCATION_RESEARCH, {"location_id": identity, "phase": "arbitrary"})
    with pytest.raises(ValueError):
        validate_payload(JobKind.LOCATION_RESEARCH, {"location_id": "missing", "phase": "initial"})


def test_search_label_preserves_resolved_parent_context():
    assert (
        search_label(
            [
                SimpleNamespace(name="United States"),
                SimpleNamespace(name="Illinois"),
                SimpleNamespace(name="Springfield"),
            ]
        )
        == "Springfield, Illinois, United States"
    )
    with pytest.raises(ValueError):
        search_label([SimpleNamespace(name="x" * 200), SimpleNamespace(name="y" * 200)])


async def test_shared_llm_budget_is_atomic_and_limits_concurrency():
    active = maximum = 0

    async def complete(**kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return Completion(requests=1)

    client = AsyncMock()
    client.complete.side_effect = complete
    limited = _LimitedClient(client, 3, concurrency=2)
    results = await asyncio.gather(*(limited.complete() for _ in range(8)))
    assert client.complete.await_count == limited.calls == 3
    assert sum(result.error == "request_limit" for result in results) == 5
    assert maximum == 2
