# ADR-005: Hybrid search, chunking and reranking

- Status: accepted
- Date: 2026-11-26

## Context

Triage needs two kinds of retrieval:
- near-duplicate issues, where exact tokens matter (error names, flags, versions);
- explanatory evidence (docs sections, maintainer answers), where semantics matter.

Everything must run locally and the choice must be measurable.

## Decision

- **One Qdrant collection, two named vectors per chunk.** `dense` is nomic-embed-text through
  Ollama, 768 dimensions, cosine, with the `search_document:` / `search_query:` task prefixes the
  model was trained with. `bm25` is the fastembed `Qdrant/bm25` sparse vector with
  `Modifier.IDF`, so Qdrant computes IDF over the live collection.
- **Hybrid = Reciprocal Rank Fusion** of the top 50 from each vector (Qdrant `FusionQuery(RRF)`).
  RRF needs no score calibration between cosine and BM25.
- **Rerank** the top 30 *documents* (not chunks) with the `BAAI/bge-reranker-base` cross-encoder.
  It runs as ONNX on CPU via fastembed (see ADR-001); 30 keeps p95 latency bounded.
- **Chunking.**
  - Issue: title + body, split on paragraphs at 1,500 chars with 200 overlap. Every comment chunk
    is prefixed with the issue title, so it stays searchable on its own.
  - Comments: maintainer comments (`OWNER`/`MEMBER`/`COLLABORATOR`) are indexed first, capped at
    20 comment chunks per thread.
  - Docs: split on `#`–`###` headings, with mkdocs-style anchors, so citations point at the
    section.
  - HTML comments (issue templates) are stripped.
- **Results are grouped per document** (`doc_key` = `repo#number` or `repo:path`), keeping the best
  chunk, so recall@k counts issues, not chunks.
- **Re-indexing replaces a document**: the old chunks are deleted by `doc_key` before the upsert,
  because the chunk count changes as comments arrive.
- **Time-split safety.** `SearchFilters.created_before` and `exclude_doc_keys` let evals and triage
  query only what existed before the issue. The retrieval eval always sets both.
- **CI uses deterministic stand-ins** with the same interfaces: a hashing embedder, a pure-Python
  BM25 term-frequency encoder and a token-overlap reranker. Unit tests run against Qdrant's local
  in-memory mode, so no Docker is needed.

## Consequences

- The variants (dense, sparse, hybrid, hybrid_rerank) are a runtime parameter, so
  `make eval-retrieval` and the search playground compare them on the same index.
- Payload indexes on `repo`, `kind`, `doc_key`, `number` and `created_ts` keep filtered queries fast
  on the server.
