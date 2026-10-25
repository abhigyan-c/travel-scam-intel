import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.embeddings import Embedder, validate_vector


@pytest.mark.parametrize(
    "vector", [[0] * 384, [1] * 383, [float("nan")] * 384, [float("inf")] * 384, [1e308] * 384]
)
def test_invalid_vectors(vector):
    with pytest.raises(ValueError):
        validate_vector(vector)


@pytest.mark.parametrize("texts", [[], [""], [" "], ["a" * 4001], ["x"] * 17])
async def test_invalid_inputs_do_not_load_model(texts):
    with pytest.raises(ValueError):
        await Embedder().embed(texts)


async def test_embedding_requires_explicit_preload():
    with pytest.raises(ValueError, match="not_preloaded"):
        await Embedder().embed(["A taxi fare report"])


async def test_token_window_never_silently_truncates():
    embedder = Embedder()
    embedder._model = Mock()
    embedder._tokenizer = SimpleNamespace(encode=lambda text: SimpleNamespace(ids=[0] * 513))
    with pytest.raises(ValueError, match="token_window"):
        await embedder.embed(["Many tokens"])
    embedder._model.embed.assert_not_called()


async def test_cancelled_thread_retains_cpu_lock_until_it_finishes():
    embedder = Embedder()
    started, release = threading.Event(), threading.Event()

    def generate(*args, **kwargs):
        started.set()
        release.wait(5)
        return [[1] + [0] * 383]

    embedder._model = SimpleNamespace(embed=generate)
    embedder._tokenizer = SimpleNamespace(encode=lambda text: SimpleNamespace(ids=[0]))
    pending = asyncio.create_task(embedder.embed(["test"]))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        with pytest.raises(ValueError, match="busy"):
            await embedder.embed(["test"])
    finally:
        release.set()
