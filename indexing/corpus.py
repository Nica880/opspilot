"""Read issues, comments and docs back from Postgres."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from psycopg_pool import AsyncConnectionPool

from ingestion.models import CommentRecord, DocRecord, IssueRecord

_ISSUE_COLS = """
i.number, i.kind, i.title, i.body, i.state, i.state_reason, i.author, i.labels, i.html_url,
i.created_at, i.updated_at, i.closed_at, i.comments_count
"""


def _issue(row: dict[str, Any]) -> IssueRecord:
    return IssueRecord.model_validate({k: row[k] for k in IssueRecord.model_fields if k in row})


def _comment(row: dict[str, Any], number: int) -> CommentRecord:
    return CommentRecord(
        id=row["id"],
        issue_number=number,
        author=row["author"],
        author_association=row["author_association"],
        body=row["body"],
        html_url=row["html_url"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class CorpusReader:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def issue(self, repo: str, number: int) -> tuple[IssueRecord, list[CommentRecord]] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT i.id, {_ISSUE_COLS} FROM issues i JOIN repos r ON r.id = i.repo_id
                    WHERE r.full_name = %s AND i.number = %s""",  # noqa: S608 - constant columns
                (repo, number),
            )
            row: Any = await cur.fetchone()
            if row is None:
                return None
            cur = await conn.execute(
                "SELECT * FROM comments WHERE issue_id = %s ORDER BY created_at", (row["id"],)
            )
            comments = [_comment(c, number) for c in await cur.fetchall()]  # type: ignore[arg-type]
        return _issue(row), comments

    async def doc(self, repo: str, path: str) -> DocRecord | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT d.path, d.url, d.title, d.content, d.sha FROM docs_pages d
                   JOIN repos r ON r.id = d.repo_id WHERE r.full_name = %s AND d.path = %s""",
                (repo, path),
            )
            row: Any = await cur.fetchone()
        return None if row is None else DocRecord.model_validate(row)

    async def all_issue_keys(self) -> list[tuple[str, int]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT r.full_name, i.number FROM issues i JOIN repos r ON r.id = i.repo_id
                   ORDER BY r.full_name, i.number"""
            )
            rows: list[Any] = await cur.fetchall()
        return [(r["full_name"], r["number"]) for r in rows]

    async def all_doc_keys(self) -> list[tuple[str, str]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT r.full_name, d.path FROM docs_pages d JOIN repos r ON r.id = d.repo_id
                   ORDER BY r.full_name, d.path"""
            )
            rows: list[Any] = await cur.fetchall()
        return [(r["full_name"], r["path"]) for r in rows]

    async def iter_issues(
        self, repo: str | None = None
    ) -> AsyncIterator[tuple[str, IssueRecord, list[CommentRecord]]]:
        for full_name, number in await self.all_issue_keys():
            if repo is not None and full_name != repo:
                continue
            found = await self.issue(full_name, number)
            if found is not None:
                yield full_name, found[0], found[1]
