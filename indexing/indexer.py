"""Turn records into chunks, embed them (dense + sparse) and write them to Qdrant."""

from __future__ import annotations

from dataclasses import dataclass

from indexing.chunking import Chunk, doc_chunks, doc_key_issue, doc_key_page, issue_chunks
from indexing.embeddings import Embedder, SparseEncoder
from indexing.qdrant_index import QdrantIndex
from ingestion.models import CommentRecord, DocRecord, IssueRecord


@dataclass
class IndexStats:
    documents: int = 0
    chunks: int = 0


class Indexer:
    def __init__(self, index: QdrantIndex, embedder: Embedder, sparse: SparseEncoder) -> None:
        self.index = index
        self.embedder = embedder
        self.sparse = sparse
        self.stats = IndexStats()

    async def _write(self, doc_key: str, chunks: list[Chunk]) -> None:
        texts = [c.text for c in chunks]
        dense = await self.embedder.embed_documents(texts) if texts else []
        sparse = await self.sparse.embed_documents(texts) if texts else []
        await self.index.replace_document(doc_key, chunks, dense, sparse)
        self.stats.documents += 1
        self.stats.chunks += len(chunks)

    async def index_issue(
        self, repo: str, issue: IssueRecord, comments: list[CommentRecord]
    ) -> None:
        await self._write(doc_key_issue(repo, issue.number), issue_chunks(repo, issue, comments))

    async def index_doc(self, repo: str, doc: DocRecord) -> None:
        await self._write(doc_key_page(repo, doc.path), doc_chunks(repo, doc))
