# ADR-009: Frontend: Next.js client views, SSE run stream, mocked-API E2E

- Status: accepted
- Date: 2026-11-26

## Context

The UI is an operator console (inbox, issue view, approval queue, search playground, evals, ops).
Everything it shows comes from the FastAPI backend and changes while agents run. React/Next.js is
the most requested frontend stack and a deliberate contrast with the Angular UI of DocFlow.

## Decision

- **Next.js 16 App Router, TypeScript strict, Tailwind 4, shadcn/ui** (base-nova style on Base
  UI). Pages are thin server components that render client views. Data comes from **TanStack
  Query** against the API (`NEXT_PUBLIC_API_URL`). No Next.js API routes or server actions, so
  the backend stays the single source of truth.
- **Cache Components** stay on (the create-next-app default). Dynamic routes read params with
  `useParams()` inside a `<Suspense>` boundary, as Next 16 requires. Browser-only state (the
  reviewer name) is read with `useSyncExternalStore`, so prerendered HTML hydrates without
  mismatch.
- **Live updates over SSE**: `GET /api/stream/runs` emits a `run` event whenever a run's status
  or step count changes. The server polls the run table every 2 s, which is simple and survives
  API restarts; Postgres LISTEN/NOTIFY can replace it later. The client only invalidates the
  matching TanStack queries, so details always come from the REST endpoints.
- **Citations are first-class UI**: `[S#]` in a draft links to the evidence item; refusals,
  verification failures and injection flags are shown, never hidden.
- **E2E with a mocked API**: Playwright runs the production build (`next start`) and fulfils
  every `/api/**` request with fixtures, including the SSE stream. It asserts the decision
  payloads the UI posts. CI needs no backend.
- **Docker** builds `output: "standalone"` only when `NEXT_OUTPUT=standalone` (set in the
  Dockerfile); local `next start` (used by Playwright) keeps the default output.
- No Google Fonts: the system font stack keeps builds offline-safe.

## Consequences

- With 2 s polling SSE, updates can arrive up to ~2 s late. That is acceptable next to a 10–40 s
  agent run.
- The UI has no auth; it is meant for localhost. The approval endpoint records the reviewer name
  it is given.
