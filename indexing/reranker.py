"""Cross-encoder reranking (bge-reranker-base, ONNX on CPU via fastembed)."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from indexing.embeddings import tokens

RERANK_MODEL = "BAAI/bge-reranker-base"


class Reranker(Protocol):
    async def scores(self, query: str, documents: list[str]) -> list[float]: ...


class FastembedReranker:
    def __init__(self, model: str = RERANK_MODEL, cache_dir: str | None = None) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._model: Any = TextCrossEncoder(model, cache_dir=cache_dir)

    async def scores(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        result = await asyncio.to_thread(lambda: list(self._model.rerank(query, documents)))
        return [float(s) for s in result]


class OverlapReranker:
    """Deterministic stand-in for CI: fraction of query tokens present in the document."""

    async def scores(self, query: str, documents: list[str]) -> list[float]:
        q = set(tokens(query))
        if not q:
            return [0.0] * len(documents)
        return [len(q & set(tokens(doc))) / len(q) for doc in documents]
