# Plan

Each milestone is shippable, tested and one commit.

| # | Milestone | Scope | Status |
|---|---|---|---|
| 1 | Foundation | layout, compose (postgres, qdrant, redpanda, temporal, phoenix), Makefile, CI, CLAUDE.md, ADR-001 | done |
| 2 | Ingestion | GitHub client (rate limits, retries, ETags), backfill + incremental cursor, events to Redpanda, recorded fixtures | done |
| 3 | Indexing + search | chunking, dense + sparse in Qdrant, reranker, search API, retrieval eval (dense vs hybrid vs rerank) | done |
| 4 | Agent | LangGraph graph, structured outputs, guardrails, Temporal workflow with approval signal, fake LLM | done |
| 5 | Evals | harness, LLM-as-judge, promptfoo in CI, red-team set, first report | done: first report is the red-team run with the fake LLM; model metrics need `make eval` |
| 6 | Frontend | all pages in Next.js, SSE streaming, Playwright E2E with a mocked API | |
| 7 | MCP + A2A | MCP tools, A2A agent card, tool-safety tests | |
| 8 | Kubernetes | Helm chart, k3d, Argo CD app-of-apps, KEDA on consumer lag, helm lint + kubeconform | |
| 9 | Polish | README, metrics table from real runs, screenshots, demo GIF | |

## Run modes

- `make up`: docker compose, used by the demo (small mode, ~2k issues from 3 repos).
- `make k8s-up`: k3d + Argo CD + Helm, same images (outside the 30-minute demo budget).

## Definition of done

See section 11 of the project brief: `make up && make demo` on a clean Mac, CI green,
20k+ issues indexed in full mode, triage on 1,000+ held-out issues, measured metrics in the README.
