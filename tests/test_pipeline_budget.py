import asyncio
from unittest.mock import AsyncMock

from app.integrations.sarvam import Completion
from app.relevance import classify_text
from app.research import _LimitedClient, _StageClient


async def test_concurrent_upstream_calls_leave_resolution_capacity():
    delegate = AsyncMock()
    delegate.complete.return_value = Completion(requests=1)
    budget = _LimitedClient(delegate, 4)
    upstream = _StageClient(budget, 1)
    results = await asyncio.gather(*(upstream.complete() for _ in range(8)))
    assert sum(r.error is None for r in results) == 3
    assert all(r.requests == 0 for r in results if r.error)
    assert (await budget.complete()).error is None
    assert (await budget.complete()).error == "request_limit"
    assert delegate.complete.await_count == budget.calls == 4


async def test_phase_with_large_allowance_still_preserves_root_headroom():
    remaining = 2

    async def reserve(kind):
        nonlocal remaining
        assert kind == "llm_calls"
        if not remaining:
            return False
        remaining -= 1
        return True

    delegate = AsyncMock()
    delegate.complete.return_value = Completion(requests=1)
    budget = _LimitedClient(delegate, 24, reserve=reserve, remaining=lambda: remaining)
    assert (await _StageClient(budget, 1).complete()).error is None
    blocked = await _StageClient(budget, 1).complete()
    assert blocked.error == "pipeline_headroom" and blocked.requests == 0
    assert remaining == 1
    assert (await budget.complete()).error is None
    assert (await budget.complete()).error == "run_llm_limit"
    assert budget.calls == 2


async def test_reserved_capacity_is_not_a_failed_provider_attempt():
    delegate = AsyncMock()
    delegate.complete.return_value = Completion(error="pipeline_headroom", requests=0)
    text = "Ordinary visitor information. " * 10
    result = await classify_text(text, delegate)
    assert not result.complete and result.assessments == []
