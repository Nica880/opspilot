"""Needs `make up`. Run with `make test-integration`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from common.config import get_settings
from common.db import close_pool, get_pool
from common.migrate import migrate
from ingestion.github import CachedResponse
from ingestion.models import CommentRecord, IssueRecord
from ingestion.store import PgHttpCache, PgStore

pytestmark = pytest.mark.integration


def _issue(number: int, title: str) -> IssueRecord:
    now = datetime(2025, 6, 1, tzinfo=UTC)
    return IssueRecord(
        number=number,
        kind="issue",
        title=title,
        body="body",
        state="open",
        state_reason=None,
        author="someone",
        labels=["bug"],
        html_url=f"https://github.com/test/it/issues/{number}",
        created_at=now,
        updated_at=now,
        closed_at=None,
        comments_count=0,
    )


async def test_pg_store_roundtrip() -> None:
    await migrate(get_settings().database_url)
    pool = await get_pool()
    try:
        store = PgStore(pool)
        repo_id = await store.upsert_repo(
            "test/it-roundtrip", license_spdx="MIT", default_branch="main", html_url="https://x"
        )
        async with pool.connection() as conn:
            await conn.execute("DELETE FROM issues WHERE repo_id = %s", (repo_id,))
        first = await store.upsert_issues(repo_id, [_issue(1, "a"), _issue(2, "b")])
        again = await store.upsert_issues(repo_id, [_issue(2, "b2")])
        assert [r.is_new for r in first] == [True, True]
        assert [r.is_new for r in again] == [False]

        comment = CommentRecord(
            id=987654321,
            issue_number=2,
            author="m",
            author_association="MEMBER",
            body="thanks",
            html_url="https://x/c",
            created_at=datetime(2025, 6, 2, tzinfo=UTC),
            updated_at=datetime(2025, 6, 2, tzinfo=UTC),
        )
        orphan = comment.model_copy(update={"id": 987654322, "issue_number": 404})
        assert await store.upsert_comments(repo_id, [comment, orphan]) == 1

        since = datetime(2025, 6, 3, tzinfo=UTC)
        await store.set_cursor(repo_id, "issues", since)
        assert await store.get_cursor(repo_id, "issues") == since

        cache = PgHttpCache(pool)
        await cache.put("https://api/x", CachedResponse('"e"', None, [{"a": 1}], None))
        cached = await cache.get("https://api/x")
        assert cached is not None and cached.data == [{"a": 1}] and cached.etag == '"e"'
    finally:
        await close_pool()
