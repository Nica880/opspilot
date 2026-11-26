"""Build search components from settings: real models locally, deterministic fakes in CI."""

from __future__ import annotations

from qdrant_client import AsyncQdrantClient

from common.config import Settings
from indexing.embeddings import (
    Embedder,
    FastembedBm25,
    HashingEmbedder,
    OllamaEmbedder,
    SimpleBm25,
    SparseEncoder,
)
from indexing.qdrant_index import QdrantIndex
from indexing.reranker import FastembedReranker, OverlapReranker, Reranker
from indexing.search import SearchService


def build_encoders(settings: Settings) -> tuple[Embedder, SparseEncoder, Reranker]:
    if settings.llm_provider == "fake":
        return HashingEmbedder(), SimpleBm25(), OverlapReranker()
    return (
        OllamaEmbedder(settings.ollama_url, settings.embed_model),
        FastembedBm25(),
        FastembedReranker(),
    )


async def build_search(
    settings: Settings, client: AsyncQdrantClient | None = None
) -> SearchService:
    embedder, sparse, reranker = build_encoders(settings)
    index = QdrantIndex(client or AsyncQdrantClient(url=settings.qdrant_url), embedder.dim)
    await index.ensure_collection()
    return SearchService(index, embedder, sparse, reranker)
