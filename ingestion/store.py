"""Persistence for ingested data: a Protocol, a Postgres implementation and an in-memory one."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from ingestion.github import CachedResponse
from ingestion.models import CommentRecord, DocRecord, IssueRecord


@dataclass(frozen=True)
class UpsertResult:
    number: int
    is_new: bool


class Store(Protocol):
    async def upsert_repo(
        self, full_name: str, *, license_spdx: str | None, default_branch: str, html_url: str
    ) -> int: ...

    async def upsert_issues(
        self, repo_id: int, issues: list[IssueRecord]
    ) -> list[UpsertResult]: ...

    async def upsert_comments(self, repo_id: int, comments: list[CommentRecord]) -> int: ...

    async def doc_sha(self, repo_id: int, path: str) -> str | None: ...

    async def upsert_doc(self, repo_id: int, doc: DocRecord) -> None: ...

    async def get_cursor(self, repo_id: int, resource: str) -> datetime | None: ...

    async def set_cursor(self, repo_id: int, resource: str, since: datetime) -> None: ...


# ---------------------------------------------------------------------------- in-memory


@dataclass
class MemoryStore:
    repos: dict[str, dict[str, Any]] = field(default_factory=dict)
    issues: dict[tuple[int, int], IssueRecord] = field(default_factory=dict)
    comments: dict[int, tuple[int, CommentRecord]] = field(default_factory=dict)
    docs: dict[tuple[int, str], DocRecord] = field(default_factory=dict)
    cursors: dict[tuple[int, str], datetime] = field(default_factory=dict)

    async def upsert_repo(
        self, full_name: str, *, license_spdx: str | None, default_branch: str, html_url: str
    ) -> int:
        repo = self.repos.setdefault(full_name, {"id": len(self.repos) + 1})
        repo.update(license_spdx=license_spdx, default_branch=default_branch, html_url=html_url)
        return int(repo["id"])

    async def upsert_issues(self, repo_id: int, issues: list[IssueRecord]) -> list[UpsertResult]:
        results = []
        for issue in issues:
            key = (repo_id, issue.number)
            results.append(UpsertResult(issue.number, key not in self.issues))
            self.issues[key] = issue
        return results

    async def upsert_comments(self, repo_id: int, comments: list[CommentRecord]) -> int:
        stored = 0
        for comment in comments:
            if (repo_id, comment.issue_number) in self.issues:
                self.comments[comment.id] = (repo_id, comment)
                stored += 1
        return stored

    async def doc_sha(self, repo_id: int, path: str) -> str | None:
        doc = self.docs.get((repo_id, path))
        return doc.sha if doc else None

    async def upsert_doc(self, repo_id: int, doc: DocRecord) -> None:
        self.docs[(repo_id, doc.path)] = doc

    async def get_cursor(self, repo_id: int, resource: str) -> datetime | None:
        return self.cursors.get((repo_id, resource))

    async def set_cursor(self, repo_id: int, resource: str, since: datetime) -> None:
        self.cursors[(repo_id, resource)] = since


# ---------------------------------------------------------------------------- postgres

_UPSERT_ISSUE = """
INSERT INTO issues (repo_id, number, kind, title, body, state, state_reason, author, labels,
                    html_url, created_at, updated_at, closed_at, comments_count)
VALUES (%(repo_id)s, %(number)s, %(kind)s, %(title)s, %(body)s, %(state)s, %(state_reason)s,
        %(author)s, %(labels)s, %(html_url)s, %(created_at)s, %(updated_at)s, %(closed_at)s,
        %(comments_count)s)
ON CONFLICT (repo_id, number) DO UPDATE SET
    kind = EXCLUDED.kind, title = EXCLUDED.title, body = EXCLUDED.body, state = EXCLUDED.state,
    state_reason = EXCLUDED.state_reason, labels = EXCLUDED.labels,
    updated_at = EXCLUDED.updated_at, closed_at = EXCLUDED.closed_at,
    comments_count = EXCLUDED.comments_count, ingested_at = now()
RETURNING number, (xmax = 0) AS inserted
"""

_UPSERT_COMMENT = """
INSERT INTO comments (id, issue_id, author, author_association, body, html_url, created_at,
                      updated_at)
SELECT %(id)s, i.id, %(author)s, %(author_association)s, %(body)s, %(html_url)s, %(created_at)s,
       %(updated_at)s
FROM issues i WHERE i.repo_id = %(repo_id)s AND i.number = %(issue_number)s
ON CONFLICT (id) DO UPDATE SET body = EXCLUDED.body, updated_at = EXCLUDED.updated_at
"""


class PgStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def upsert_repo(
        self, full_name: str, *, license_spdx: str | None, default_branch: str, html_url: str
    ) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO repos (full_name, license_spdx, default_branch, html_url)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (full_name) DO UPDATE SET license_spdx = EXCLUDED.license_spdx,
                    default_branch = EXCLUDED.default_branch, html_url = EXCLUDED.html_url
                RETURNING id
                """,
                (full_name, license_spdx, default_branch, html_url),
            )
            row = await cur.fetchone()
        assert row is not None
        return int(_col(row, "id"))

    async def upsert_issues(self, repo_id: int, issues: list[IssueRecord]) -> list[UpsertResult]:
        results: list[UpsertResult] = []
        async with self._pool.connection() as conn, conn.transaction():
            for issue in issues:
                cur = await conn.execute(_UPSERT_ISSUE, {"repo_id": repo_id, **issue.model_dump()})
                row = await cur.fetchone()
                assert row is not None
                results.append(UpsertResult(int(_col(row, "number")), bool(_col(row, "inserted"))))
        return results

    async def upsert_comments(self, repo_id: int, comments: list[CommentRecord]) -> int:
        stored = 0
        async with self._pool.connection() as conn, conn.transaction():
            for comment in comments:
                cur = await conn.execute(
                    _UPSERT_COMMENT, {"repo_id": repo_id, **comment.model_dump()}
                )
                stored += cur.rowcount
        return stored

    async def doc_sha(self, repo_id: int, path: str) -> str | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT sha FROM docs_pages WHERE repo_id = %s AND path = %s", (repo_id, path)
            )
            row = await cur.fetchone()
        return None if row is None else str(_col(row, "sha"))

    async def upsert_doc(self, repo_id: int, doc: DocRecord) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO docs_pages (repo_id, path, url, title, content, sha)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (repo_id, path) DO UPDATE SET url = EXCLUDED.url,
                    title = EXCLUDED.title, content = EXCLUDED.content, sha = EXCLUDED.sha,
                    ingested_at = now()
                """,
                (repo_id, doc.path, doc.url, doc.title, doc.content, doc.sha),
            )

    async def get_cursor(self, repo_id: int, resource: str) -> datetime | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT since FROM ingest_cursors WHERE repo_id = %s AND resource = %s",
                (repo_id, resource),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        since = _col(row, "since")
        return since if isinstance(since, datetime) else None

    async def set_cursor(self, repo_id: int, resource: str, since: datetime) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO ingest_cursors (repo_id, resource, since) VALUES (%s, %s, %s)
                ON CONFLICT (repo_id, resource) DO UPDATE SET since = EXCLUDED.since,
                    updated_at = now()
                """,
                (repo_id, resource, since),
            )


class PgHttpCache:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def get(self, url: str) -> CachedResponse | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT etag, last_modified, body FROM http_cache WHERE url = %s", (url,)
            )
            row = await cur.fetchone()
        if row is None:
            return None
        body = _col(row, "body")
        body = json.loads(body) if isinstance(body, str) else body
        return CachedResponse(
            etag=_col(row, "etag"),
            last_modified=_col(row, "last_modified"),
            data=body["data"],
            link=body.get("link"),
        )

    async def put(self, url: str, response: CachedResponse) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO http_cache (url, etag, last_modified, body) VALUES (%s, %s, %s, %s)
                ON CONFLICT (url) DO UPDATE SET etag = EXCLUDED.etag,
                    last_modified = EXCLUDED.last_modified, body = EXCLUDED.body,
                    fetched_at = now()
                """,
                (
                    url,
                    response.etag,
                    response.last_modified,
                    Jsonb({"data": response.data, "link": response.link}),
                ),
            )


def _col(row: Any, name: str) -> Any:
    return row[name] if isinstance(row, dict) else getattr(row, name)
