"""Search over the index: dense, hybrid (RRF) or hybrid + cross-encoder rerank."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from indexing.embeddings import Embedder, SparseEncoder
from indexing.qdrant_index import ChunkHit, Mode, QdrantIndex, SearchFilters
from indexing.reranker import Reranker

Variant = Literal["dense", "sparse", "hybrid", "hybrid_rerank"]
VARIANTS: tuple[Variant, ...] = ("dense", "hybrid", "hybrid_rerank")
RERANK_CANDIDATES = 30
CHUNKS_PER_RESULT = 4
MAX_QUERY_CHARS = 2000


@dataclass
class SearchHit:
    """One document (issue/PR/discussion/doc page) with its best-matching chunk."""

    doc_key: str
    score: float
    kind: str
    repo: str
    title: str
    url: str
    text: str
    number: int | None = None
    path: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


def group_hits(hits: list[ChunkHit]) -> list[SearchHit]:
    best: dict[str, SearchHit] = {}
    for hit in hits:
        p = hit.payload
        key = str(p["doc_key"])
        if key in best and best[key].score >= hit.score:
            continue
        best[key] = SearchHit(
            doc_key=key,
            score=hit.score,
            kind=str(p.get("kind", "")),
            repo=str(p.get("repo", "")),
            title=str(p.get("title", "")),
            url=str(p.get("url", "")),
            text=str(p.get("text", "")),
            number=p.get("number"),
            path=p.get("path"),
            payload=p,
        )
    return sorted(best.values(), key=lambda h: h.score, reverse=True)


class SearchService:
    def __init__(
        self,
        index: QdrantIndex,
        embedder: Embedder,
        sparse: SparseEncoder,
        reranker: Reranker | None = None,
    ) -> None:
        self.index = index
        self.embedder = embedder
        self.sparse = sparse
        self.reranker = reranker

    async def search(
        self,
        query: str,
        *,
        variant: Variant = "hybrid_rerank",
        k: int = 10,
        filters: SearchFilters | None = None,
    ) -> list[SearchHit]:
        query = query[:MAX_QUERY_CHARS]
        filters = filters or SearchFilters()
        mode: Mode = "hybrid" if variant == "hybrid_rerank" else variant
        rerank = variant == "hybrid_rerank"
        dense = await self.embedder.embed_query(query) if mode in ("dense", "hybrid") else None
        sparse = await self.sparse.embed_query(query) if mode in ("sparse", "hybrid") else None
        want = max(k, RERANK_CANDIDATES) if rerank else k
        chunks = await self.index.query(
            mode=mode, dense=dense, sparse=sparse, filters=filters, limit=want * CHUNKS_PER_RESULT
        )
        hits = group_hits(chunks)[:want]
        if rerank:
            if self.reranker is None:
                raise ValueError("hybrid_rerank needs a reranker")
            scores = await self.reranker.scores(query, [h.text for h in hits])
            for hit, score in zip(hits, scores, strict=True):
                hit.score = score
            hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]
