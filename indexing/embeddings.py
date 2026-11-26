"""Dense embeddings (Ollama nomic-embed-text) and sparse BM25 vectors (fastembed)."""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import zlib
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

_TOKEN = re.compile(r"[a-z0-9_]+")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
        "you",
        "your",
        "we",
        "they",
    ]
)


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


class Embedder(Protocol):
    dim: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class OllamaEmbedder:
    """nomic-embed-text expects task prefixes; without them retrieval quality drops."""

    dim = 768

    def __init__(self, base_url: str, model: str = "nomic-embed-text", batch: int = 32) -> None:
        self._http = httpx.AsyncClient(base_url=base_url, timeout=120.0)
        self._model = model
        self._batch = batch

    async def _embed(self, inputs: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(inputs), self._batch):
            response = await self._http.post(
                "/api/embed",
                json={"model": self._model, "input": inputs[start : start + self._batch]},
            )
            response.raise_for_status()
            out.extend(response.json()["embeddings"])
        return out

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed([f"search_document: {t}" for t in texts])

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([f"search_query: {text}"]))[0]

    async def aclose(self) -> None:
        await self._http.aclose()


class HashingEmbedder:
    """Deterministic bag-of-words hashing embedder for tests and CI (no model download)."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in tokens(text):
            digest = hashlib.blake2b(tok.encode(), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dim
            vec[idx] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@dataclass(frozen=True)
class Sparse:
    indices: list[int]
    values: list[float]


class SparseEncoder(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[Sparse]: ...

    async def embed_query(self, text: str) -> Sparse: ...


class SimpleBm25:
    """BM25 term-frequency part; Qdrant applies IDF (`Modifier.IDF`) at query time.

    Mirrors what fastembed's `Qdrant/bm25` does, without stemming, for offline tests.
    """

    def __init__(self, k: float = 1.2, b: float = 0.75, avg_len: float = 256.0) -> None:
        self.k, self.b, self.avg_len = k, b, avg_len

    @staticmethod
    def _index(token: str) -> int:
        return zlib.crc32(token.encode()) & 0x7FFFFFFF

    def _doc(self, text: str) -> Sparse:
        toks = tokens(text)
        counts = Counter(toks)
        norm = self.k * (1 - self.b + self.b * len(toks) / self.avg_len)
        merged: dict[int, float] = {}
        for tok, tf in counts.items():
            merged[self._index(tok)] = tf * (self.k + 1) / (tf + norm)
        return Sparse(list(merged), list(merged.values()))

    async def embed_documents(self, texts: list[str]) -> list[Sparse]:
        return [self._doc(t) for t in texts]

    async def embed_query(self, text: str) -> Sparse:
        idx = sorted({self._index(t) for t in tokens(text)})
        return Sparse(idx, [1.0] * len(idx))


class FastembedBm25:
    def __init__(self, cache_dir: str | None = None) -> None:
        from fastembed import SparseTextEmbedding

        self._model: Any = SparseTextEmbedding("Qdrant/bm25", cache_dir=cache_dir)

    @staticmethod
    def _convert(emb: Any) -> Sparse:
        return Sparse([int(i) for i in emb.indices], [float(v) for v in emb.values])

    async def embed_documents(self, texts: list[str]) -> list[Sparse]:
        embs = await asyncio.to_thread(lambda: list(self._model.embed(texts)))
        return [self._convert(e) for e in embs]

    async def embed_query(self, text: str) -> Sparse:
        embs = await asyncio.to_thread(lambda: list(self._model.query_embed(text)))
        return self._convert(embs[0])
