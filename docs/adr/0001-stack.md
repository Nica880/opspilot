# ADR-001: Technology stack

- Status: accepted
- Date: 2026-11-26

## Context

OpsPilot is a portfolio project that must run free and fully local on an Apple Silicon laptop
(M3 Pro, 36 GB RAM, Docker via Colima 6 CPU / 14 GB, Ollama native on the host). It must show
skills a sibling project (DocFlow: Airflow, dbt, DuckDB, pgvector, Angular) does not: LangGraph,
Temporal, Kubernetes, React/Next.js, event streaming, hybrid search with reranking, LLM
observability, evals in CI and agent-to-agent protocols.

## Decision

| Concern | Choice |
|---|---|
| Agent orchestration | LangGraph, checkpoints in Postgres (`langgraph-checkpoint-postgres`) |
| Durable workflows | Temporal, self-hosted (`temporalio/auto-setup` on the shared Postgres) |
| Event streaming | Redpanda single node (Kafka API, `aiokafka` client) |
| Search | Qdrant hybrid: dense `nomic-embed-text` (Ollama) + sparse BM25 (fastembed), cross-encoder rerank (`BAAI/bge-reranker-base` via fastembed ONNX) |
| API | FastAPI + Pydantic v2, async, SSE (`sse-starlette`) |
| Frontend | Next.js 16 App Router, TypeScript strict, Tailwind, TanStack Query |
| Observability | OpenTelemetry → Arize Phoenix (OpenInference instrumentation) |
| Evals | pytest harness + promptfoo |
| Protocols | MCP server (`mcp` SDK) + A2A agent card |
| Runtime | docker compose for dev/demo; k3d + Helm + Argo CD + KEDA for the k8s milestone |
| State | PostgreSQL 16: app state, LangGraph checkpoints, Temporal persistence, Phoenix |
| Tooling | uv, ruff, mypy strict, ESLint, Prettier, Vitest, Playwright, gitleaks |

## Consequences

- One Postgres server holds four logical databases (`opspilot`, `temporal`,
  `temporal_visibility`, `phoenix`). Fewer moving parts, but a noisy neighbour can slow the rest;
  acceptable for a single-user laptop deployment.
- The reranker runs as ONNX on CPU through fastembed instead of PyTorch on MPS. The image is about
  2 GB smaller and the code is the same in Docker (Linux, no MPS) and on the host. Cost: rerank
  latency is higher than MPS would give, so we rerank only the top 30 candidates.
- Python packages are resolved by uv and locked in `uv.lock`. The repo is an application, not a
  library (`tool.uv.package = false`); modules are imported from the repo root.
