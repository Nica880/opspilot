# ADR-004: Ingestion strategy

- Status: accepted
- Date: 2026-11-26

## Context

We need ~2k issues for the demo (small mode) and 20k+ for full evals, from fastapi/fastapi,
pydantic/pydantic and astral-sh/uv, without breaking GitHub's limits (REST 5,000 requests/hour,
GraphQL 5,000 points/hour) or its terms, with a read-only token.

## Decision

- **REST for issues, PRs and comments.** `GET /repos/{r}/issues?state=all` returns issues and PRs
  in one paginated stream (100 per page). Comments come from the repo-wide
  `/issues/comments?since=` endpoint instead of one call per issue: 1 request per 100 comments
  instead of 1 per issue.
- **GraphQL only for discussions**, which REST does not expose. The query asks for `rateLimit` and
  the client tracks the remaining points.
- **Conditional requests.** Every GET sends the stored `ETag` / `Last-Modified`; a `304` reuses
  the cached body from `http_cache` and does not count against the primary rate limit.
- **Rate limits.** The client pauses until `x-ratelimit-reset` when fewer than 50 requests remain,
  honours `Retry-After` on secondary limits (403/429), and retries 5xx and network errors with
  exponential backoff (max 5 attempts).
- **Backfill then cursor.** Backfill reads newest-first (`sort=created&direction=desc`), capped at
  700 per repo in small mode. It stores `max(updated_at)` as the cursor. Incremental runs ask for
  `sort=updated&direction=asc&since=<cursor>`, so a crash mid-run only repeats work.
- **Docs** come from the git trees + blobs APIs. A page is re-fetched only when its blob sha
  changes. Small mode caps docs at 150 pages per repo.
- **Events.** Every stored issue emits `issue.opened` (new row) or `issue.updated` to `issues.v1`,
  keyed by `repo#number`, so one issue's events stay ordered in one partition. Backfill events
  carry `backfill=true`; the triage trigger ignores them. Docs emit `doc.upserted` to `docs.v1`.
- **Licenses.** `repos.license_spdx` stores each repo's SPDX license (MIT, MIT, Apache-2.0 at the
  time of writing).
- **Fixtures** are recorded unauthenticated by `scripts/record_fixtures.py`, so a write token can
  never end up in a fixture.

## Consequences

- Budget for full mode: ~55k issues + PRs across the 3 repos ≈ 550 pages, plus comments, well
  under one hour of REST budget. Re-runs are mostly 304s.
- Small mode takes the newest 700 per repo. The eval time-split in milestone 5 picks its cutoff
  inside that window.
