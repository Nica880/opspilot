"""Qdrant collection with a dense and a sparse (BM25, IDF modifier) named vector."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from qdrant_client import AsyncQdrantClient, models

from indexing.chunking import Chunk
from indexing.embeddings import Sparse

COLLECTION = "opspilot_chunks"
DENSE = "dense"
SPARSE = "bm25"
PREFETCH_LIMIT = 50

Mode = Literal["dense", "sparse", "hybrid"]


@dataclass(frozen=True)
class SearchFilters:
    repos: Sequence[str] | None = None
    kinds: Sequence[str] | None = None
    created_before: datetime | None = None
    exclude_doc_keys: Sequence[str] = ()

    def to_qdrant(self) -> models.Filter | None:
        must: list[models.Condition] = []
        must_not: list[models.Condition] = []
        if self.repos:
            must.append(
                models.FieldCondition(key="repo", match=models.MatchAny(any=list(self.repos)))
            )
        if self.kinds:
            must.append(
                models.FieldCondition(key="kind", match=models.MatchAny(any=list(self.kinds)))
            )
        if self.created_before is not None:
            # Docs have no created_ts; this filter is for issue-only time-split queries.
            must.append(
                models.FieldCondition(
                    key="created_ts", range=models.Range(lt=self.created_before.timestamp())
                )
            )
        if self.exclude_doc_keys:
            must_not.append(
                models.FieldCondition(
                    key="doc_key", match=models.MatchAny(any=list(self.exclude_doc_keys))
                )
            )
        if not must and not must_not:
            return None
        return models.Filter(must=must or None, must_not=must_not or None)


@dataclass(frozen=True)
class ChunkHit:
    id: str
    score: float
    payload: dict[str, Any]


class QdrantIndex:
    def __init__(self, client: AsyncQdrantClient, dim: int, collection: str = COLLECTION) -> None:
        self.client = client
        self.dim = dim
        self.collection = collection

    async def ensure_collection(self) -> None:
        if await self.client.collection_exists(self.collection):
            return
        await self.client.create_collection(
            self.collection,
            vectors_config={
                DENSE: models.VectorParams(size=self.dim, distance=models.Distance.COSINE)
            },
            sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
        for field, schema in (
            ("repo", models.PayloadSchemaType.KEYWORD),
            ("kind", models.PayloadSchemaType.KEYWORD),
            ("doc_key", models.PayloadSchemaType.KEYWORD),
            ("number", models.PayloadSchemaType.INTEGER),
            ("created_ts", models.PayloadSchemaType.FLOAT),
        ):
            await self.client.create_payload_index(self.collection, field, schema)

    async def replace_document(
        self,
        doc_key: str,
        chunks: list[Chunk],
        dense: list[list[float]],
        sparse: list[Sparse],
    ) -> None:
        """Delete the document's old chunks, then upsert the new ones (chunk counts change)."""
        await self.client.delete(
            self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(key="doc_key", match=models.MatchValue(value=doc_key))
                    ]
                )
            ),
        )
        if not chunks:
            return
        await self.client.upsert(
            self.collection,
            points=[
                models.PointStruct(
                    id=chunk.id,
                    vector={
                        DENSE: d,
                        SPARSE: models.SparseVector(indices=s.indices, values=s.values),
                    },
                    payload={**chunk.payload, "text": chunk.text},
                )
                for chunk, d, s in zip(chunks, dense, sparse, strict=True)
            ],
        )

    async def query(
        self,
        *,
        mode: Mode,
        dense: list[float] | None,
        sparse: Sparse | None,
        filters: SearchFilters,
        limit: int,
    ) -> list[ChunkHit]:
        query_filter = filters.to_qdrant()
        sparse_vec = (
            models.SparseVector(indices=sparse.indices, values=sparse.values) if sparse else None
        )
        if mode == "dense":
            response = await self.client.query_points(
                self.collection, query=dense, using=DENSE, query_filter=query_filter, limit=limit
            )
        elif mode == "sparse":
            response = await self.client.query_points(
                self.collection,
                query=sparse_vec,
                using=SPARSE,
                query_filter=query_filter,
                limit=limit,
            )
        else:
            response = await self.client.query_points(
                self.collection,
                prefetch=[
                    models.Prefetch(
                        query=dense, using=DENSE, filter=query_filter, limit=PREFETCH_LIMIT
                    ),
                    models.Prefetch(
                        query=sparse_vec, using=SPARSE, filter=query_filter, limit=PREFETCH_LIMIT
                    ),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=limit,
            )
        return [
            ChunkHit(id=str(point.id), score=float(point.score), payload=dict(point.payload or {}))
            for point in response.points
        ]

    async def count(self) -> int:
        return (await self.client.count(self.collection, exact=True)).count
