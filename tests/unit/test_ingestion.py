from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from ingestion.events import ISSUES_TOPIC, MemoryPublisher
from ingestion.github import GitHubClient, GitHubError, MemoryHttpCache, next_link
from ingestion.models import IssueEvent
from ingestion.pipeline import (
    _discussion_records,
    backfill_repo,
    doc_url,
    incremental_repo,
    ingest_docs,
)
from ingestion.store import MemoryStore
from tests.conftest import load_github_fixture

Handler = Callable[[httpx.Request], httpx.Response]


class FakeTime:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(handler: Handler, time: FakeTime | None = None, **kwargs: Any) -> GitHubClient:
    time = time or FakeTime()
    return GitHubClient(
        "test-token",
        cache=MemoryHttpCache(),
        transport=httpx.MockTransport(handler),
        sleep=time.sleep,
        clock=time.clock,
        **kwargs,
    )


def fixture_response(name: str, **overrides: Any) -> httpx.Response:
    recorded = load_github_fixture(name)
    headers = {k: v for k, v in recorded["headers"].items() if k != "link"}
    headers.update(overrides)
    return httpx.Response(200, json=recorded["body"], headers=headers)


def recorded_repo_handler(repo: str, issues_fixture: str, comments_body: Any = ()) -> Handler:
    slug = repo.replace("/", "_").replace("-", "_")

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/repos/{repo}":
            return fixture_response(f"repos_{slug}.json")
        if path == f"/repos/{repo}/issues":
            return fixture_response(issues_fixture)
        if path == f"/repos/{repo}/issues/comments":
            return httpx.Response(200, json=list(comments_body))
        return httpx.Response(404, json={"message": "not recorded"})

    return handler


# ------------------------------------------------------------------------------ client


async def test_conditional_request_uses_etag_and_returns_cached_body_on_304() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("if-none-match"))
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, json={"n": 1}, headers={"etag": '"v1"'})

    async with make_client(handler) as gh:
        first = await gh.get("/repos/a/b")
        second = await gh.get("/repos/a/b")
    assert seen == [None, '"v1"']
    assert first.data == second.data == {"n": 1}
    assert gh.stats.not_modified == 1


async def test_primary_rate_limit_waits_until_reset_then_retries() -> None:
    time = FakeTime()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                403,
                json={"message": "API rate limit exceeded"},
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(time.now + 120)},
            )
        return httpx.Response(200, json={"ok": True})

    async with make_client(handler, time) as gh:
        response = await gh.get("/rate")
    assert response.data == {"ok": True}
    assert time.sleeps == [121]


async def test_secondary_rate_limit_honours_retry_after() -> None:
    time = FakeTime()
    responses = iter(
        [httpx.Response(429, headers={"retry-after": "7"}), httpx.Response(200, json=[])]
    )
    async with make_client(lambda _: next(responses), time) as gh:
        await gh.get("/x")
    assert time.sleeps == [7]


async def test_low_budget_pauses_before_next_request() -> None:
    time = FakeTime()
    reset = time.now + 300

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={},
            headers={"x-ratelimit-remaining": "10", "x-ratelimit-reset": str(reset)},
        )

    async with make_client(handler, time, min_remaining=50) as gh:
        await gh.get("/a")
        await gh.get("/b")
    assert time.sleeps == [301]
    assert gh.stats.rate_limit_waits == 1


async def test_low_budget_threshold_scales_with_small_limits() -> None:
    headers = {
        "x-ratelimit-remaining": "10",
        "x-ratelimit-reset": "9999999",
        "x-ratelimit-limit": "60",
    }
    time = FakeTime()
    async with make_client(lambda _: httpx.Response(200, json={}, headers=headers), time) as gh:
        await gh.get("/a")
        await gh.get("/b")
    assert time.sleeps == []


async def test_server_errors_are_retried_with_exponential_backoff() -> None:
    time = FakeTime()
    responses = iter(
        [httpx.Response(502), httpx.Response(503), httpx.Response(200, json={"ok": 1})]
    )
    async with make_client(lambda _: next(responses), time, backoff_base=2.0) as gh:
        assert (await gh.get("/x")).data == {"ok": 1}
    assert time.sleeps == [2.0, 4.0]
    assert gh.stats.retries == 2


async def test_gives_up_after_max_retries() -> None:
    async with make_client(lambda _: httpx.Response(500), max_retries=2) as gh:
        with pytest.raises(GitHubError):
            await gh.get("/x")


async def test_paginate_follows_link_header_and_stops_at_max_items() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        link = f'<https://api.github.com/items?per_page=100&page={page + 1}>; rel="next"'
        return httpx.Response(
            200, json=[{"i": page * 10 + j} for j in range(3)], headers={"link": link}
        )

    async with make_client(handler) as gh:
        items = [item["i"] async for item in gh.paginate("/items", max_items=7)]
    assert items == [10, 11, 12, 20, 21, 22, 30]


def test_next_link_parsing() -> None:
    header = '<https://x/?page=2>; rel="next", <https://x/?page=9>; rel="last"'
    assert next_link(header) == "https://x/?page=2"
    assert next_link('<https://x/?page=1>; rel="prev"') is None


# ------------------------------------------------------------------------------ pipeline


async def test_backfill_from_recorded_pydantic_issues() -> None:
    comments = load_github_fixture(
        "repos_pydantic_pydantic_issues_comments__sort_updated_direction_desc_per_page_5.json"
    )["body"]
    handler = recorded_repo_handler(
        "pydantic/pydantic",
        "repos_pydantic_pydantic_issues__state_all_sort_created_direction_desc_per_page_5.json",
        comments,
    )
    store, publisher = MemoryStore(), MemoryPublisher()
    async with make_client(handler) as gh:
        stats = await backfill_repo(gh, store, publisher, "pydantic/pydantic", limit=10)

    assert store.repos["pydantic/pydantic"]["license_spdx"] == "MIT"
    assert stats.issues == stats.new_issues == 5
    kinds = {issue.number: issue.kind for issue in store.issues.values()}
    assert kinds[14421] == "issue" and kinds[14420] == "pull_request"
    # Comments on issue 14410 are not stored: that issue was not backfilled.
    assert {c.issue_number for _, c in store.comments.values()} == {14420}
    events = [event for topic, _, event in publisher.sent if topic == ISSUES_TOPIC]
    assert len(events) == 5
    assert all(
        isinstance(e, IssueEvent) and e.backfill and e.type == "issue.opened" for e in events
    )
    newest = max(issue.updated_at for issue in store.issues.values())
    assert store.cursors[(1, "issues")] == newest


async def test_backfill_respects_limit() -> None:
    handler = recorded_repo_handler(
        "astral-sh/uv",
        "repos_astral_sh_uv_issues__state_all_sort_created_direction_desc_per_page_5.json",
    )
    store = MemoryStore()
    async with make_client(handler) as gh:
        stats = await backfill_repo(gh, store, MemoryPublisher(), "astral-sh/uv", limit=2)
    assert stats.issues == 2
    assert store.repos["astral-sh/uv"]["license_spdx"] == "Apache-2.0"


async def test_incremental_sends_since_and_emits_opened_only_for_new_issues() -> None:
    base = load_github_fixture(
        "repos_fastapi_fastapi_issues__state_all_sort_created_direction_desc_per_page_5.json"
    )["body"]
    store, publisher = MemoryStore(), MemoryPublisher()
    async with make_client(
        recorded_repo_handler(
            "fastapi/fastapi",
            "repos_fastapi_fastapi_issues__state_all_sort_created_direction_desc_per_page_5.json",
        )
    ) as gh:
        await backfill_repo(gh, store, MemoryPublisher(), "fastapi/fastapi")
    cursor = store.cursors[(1, "issues")]

    later = (cursor + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = {**base[0], "updated_at": later, "title": "edited title"}
    new = {**base[0], "number": 99999, "created_at": later, "updated_at": later}
    since_params: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/fastapi/fastapi":
            return fixture_response("repos_fastapi_fastapi.json")
        if request.url.path.endswith("/issues"):
            since_params.append(request.url.params.get("since"))
            return httpx.Response(200, json=[updated, new])
        return httpx.Response(200, json=[])

    async with make_client(handler) as gh:
        stats = await incremental_repo(gh, store, publisher, "fastapi/fastapi")

    assert since_params == [cursor.strftime("%Y-%m-%dT%H:%M:%SZ")]
    assert stats.new_issues == 1
    types = {
        (e.number, e.type, e.backfill) for _, _, e in publisher.sent if isinstance(e, IssueEvent)
    }
    assert types == {(base[0]["number"], "issue.updated", False), (99999, "issue.opened", False)}
    assert store.cursors[(1, "issues")] == datetime.fromisoformat(later)
    assert store.issues[(1, base[0]["number"])].title == "edited title"


async def test_incremental_requires_a_backfill_first() -> None:
    async with make_client(
        recorded_repo_handler("fastapi/fastapi", "repos_fastapi_fastapi.json")
    ) as gh:
        with pytest.raises(RuntimeError, match="backfill first"):
            await incremental_repo(gh, MemoryStore(), MemoryPublisher(), "fastapi/fastapi")


async def test_docs_ingestion_skips_unchanged_blobs() -> None:
    blob_calls: list[str] = []
    content = base64.b64encode(b"# Dependencies\n\nUse Depends().").decode()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/fastapi/fastapi":
            return fixture_response("repos_fastapi_fastapi.json")
        if path == "/repos/fastapi/fastapi/git/trees/master":
            tree = [
                {
                    "path": "docs/en/docs/tutorial/dependencies/index.md",
                    "type": "blob",
                    "sha": "s1",
                },
                {"path": "docs/en/docs/img/logo.png", "type": "blob", "sha": "s2"},
                {"path": "docs/de/docs/index.md", "type": "blob", "sha": "s3"},
            ]
            return httpx.Response(200, json={"tree": tree})
        if path.startswith("/repos/fastapi/fastapi/git/blobs/"):
            blob_calls.append(path)
            return httpx.Response(200, json={"content": content, "encoding": "base64"})
        return httpx.Response(404)

    store, publisher = MemoryStore(), MemoryPublisher()
    async with make_client(handler) as gh:
        first = await ingest_docs(gh, store, publisher, "fastapi/fastapi")
        second = await ingest_docs(gh, store, publisher, "fastapi/fastapi")
    assert (first.docs, second.docs) == (1, 0)
    assert len(blob_calls) == 1
    doc = next(iter(store.docs.values()))
    assert doc.title == "Dependencies"
    assert doc.url == "https://fastapi.tiangolo.com/tutorial/dependencies/"


@pytest.mark.parametrize(
    ("repo", "path", "url"),
    [
        ("fastapi/fastapi", "docs/en/docs/index.md", "https://fastapi.tiangolo.com/"),
        (
            "pydantic/pydantic",
            "docs/concepts/models.md",
            "https://docs.pydantic.dev/latest/concepts/models/",
        ),
        ("astral-sh/uv", "docs/guides/projects.md", "https://docs.astral.sh/uv/guides/projects/"),
    ],
)
def test_doc_url_mapping(repo: str, path: str, url: str) -> None:
    assert doc_url(repo, path) == url


def test_discussion_node_mapping() -> None:
    node = json.loads(
        """{"number": 7, "title": "How to?", "body": "q", "url": "https://github.com/o/r/discussions/7",
        "createdAt": "2025-01-01T00:00:00Z", "updatedAt": "2025-01-02T00:00:00Z", "closed": true,
        "closedAt": "2025-01-03T00:00:00Z", "author": null, "labels": {"nodes": [{"name": "question"}]},
        "comments": {"totalCount": 2, "nodes": [
          {"databaseId": 11, "body": "a", "url": "u", "createdAt": "2025-01-01T01:00:00Z",
           "updatedAt": "2025-01-01T01:00:00Z", "authorAssociation": "MEMBER", "author": {"login": "m"}},
          {"databaseId": null, "body": "ghost", "url": "u2", "createdAt": "2025-01-01T01:00:00Z",
           "updatedAt": "2025-01-01T01:00:00Z", "authorAssociation": "NONE", "author": null}]}}"""
    )
    issue, comments = _discussion_records(node)
    assert (issue.kind, issue.state, issue.labels, issue.author) == (
        "discussion",
        "closed",
        ["question"],
        None,
    )
    assert issue.created_at == datetime(2025, 1, 1, tzinfo=UTC)
    assert [c.id for c in comments] == [11]
