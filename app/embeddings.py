"""Bounded local CPU embeddings; model downloads happen only during explicit preload."""

import asyncio
import hashlib
import math
import threading
from importlib.metadata import version
from pathlib import Path

MODEL = "BAAI/bge-small-en-v1.5"
REPOSITORY = "Qdrant/bge-small-en-v1.5-onnx-Q"
MODEL_REVISION = "aa8f8b060edb00e03bfdd08813a2949946c8ba55"
REVISION = f"{MODEL_REVISION}:fastembed-{version('fastembed')}:text-v1"
DIMENSIONS = 384
FILES = [
    "model_optimized.onnx",
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.txt",
]


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def validate_vector(values) -> list[float]:
    vector = [float(value) for value in values]
    if len(vector) != DIMENSIONS or not all(math.isfinite(value) for value in vector):
        raise ValueError("invalid_embedding_dimensions_or_values")
    norm = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(norm) or norm <= 0:
        raise ValueError("invalid_embedding_norm")
    return [value / norm for value in vector]


class Embedder:
    model = MODEL
    revision = REVISION

    def __init__(self, cache_dir: str = ".tools/embedding-cache"):
        self.cache_dir = Path(cache_dir)
        self._model = None
        self._tokenizer = None
        self._lock = threading.Lock()

    async def preload(self, *, download: bool = False):
        await asyncio.to_thread(self._preload, download)

    def _preload(self, download):
        if not self._lock.acquire(blocking=False):
            raise ValueError("embedding_busy")
        try:
            if self._model is not None:
                return
            import onnxruntime

            # Local embedding inference must not opt users into runtime telemetry.
            onnxruntime.disable_telemetry_events()
            from fastembed import TextEmbedding
            from huggingface_hub import snapshot_download
            from tokenizers import Tokenizer

            path = snapshot_download(
                REPOSITORY,
                revision=MODEL_REVISION,
                cache_dir=str(self.cache_dir),
                allow_patterns=FILES,
                local_files_only=not download,
            )
            tokenizer = Tokenizer.from_file(str(Path(path) / "tokenizer.json"))
            tokenizer.no_truncation()
            tokenizer.no_padding()
            model = TextEmbedding(
                MODEL,
                specific_model_path=path,
                threads=2,
                providers=["CPUExecutionProvider"],
            )
            self._tokenizer, self._model = tokenizer, model
        finally:
            self._lock.release()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not 1 <= len(texts) <= 16:
            raise ValueError("embedding_batch_must_contain_1_to_16_texts")
        if any(not isinstance(text, str) or not text.strip() or len(text) > 4000 for text in texts):
            raise ValueError("invalid_embedding_text")
        return await asyncio.to_thread(self._embed, list(texts))

    def _embed(self, texts):
        if not self._lock.acquire(blocking=False):
            raise ValueError("embedding_busy")
        try:
            if self._model is None:
                raise ValueError("embedding_model_not_preloaded")
            if any(len(self._tokenizer.encode(text).ids) > 512 for text in texts):
                raise ValueError("embedding_input_exceeds_token_window")
            # BGE v1.5 uses the same unprefixed representation for both sides of claim matching.
            vectors = [
                validate_vector(vector) for vector in self._model.embed(texts, batch_size=16)
            ]
            if len(vectors) != len(texts):
                raise ValueError("embedding_count_mismatch")
            return vectors
        finally:
            self._lock.release()
