"""Backfill, incremental sync and docs ingestion for one repository."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ingestion.events import Publisher
from ingestion.github import GitHubClient
from ingestion.models import (
    CommentRecord,
    DocEvent,
    DocRecord,
    GhComment,
    GhIssue,
    GhRepo,
    IssueEvent,
    IssueRecord,
)
from ingestion.store import Store

log = logging.getLogger(__name__)

BATCH = 100

# Where each project keeps its docs, and how a docs path maps to the published site.
DOCS_SOURCES: dict[str, tuple[str, str]] = {
    "fastapi/fastapi": ("docs/en/docs/", "https://fastapi.tiangolo.com/"),
    "pydantic/pydantic": ("docs/", "https://docs.pydantic.dev/latest/"),
    "astral-sh/uv": ("docs/", "https://docs.astral.sh/uv/"),
}

DISCUSSIONS_QUERY = """
query($owner: String!, $name: String!, $first: Int!, $after: String) {
  rateLimit { cost remaining resetAt }
  repository(owner: $owner, name: $name) {
    discussions(first: $first, after: $after, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title body url createdAt updatedAt closed closedAt
        author { login }
        labels(first: 10) { nodes { name } }
        comments(first: 20) {
          totalCount
          nodes { databaseId body url createdAt updatedAt authorAssociation author { login } }
        }
      }
    }
  }
}
"""


@dataclass
class SyncStats:
    repo: str
    issues: int = 0
    new_issues: int = 0
    comments: int = 0
    docs: int = 0
    events: int = 0


async def ensure_repo(gh: GitHubClient, store: Store, full_name: str) -> tuple[int, GhRepo]:
    repo = GhRepo.model_validate((await gh.get(f"/repos/{full_name}")).data)
    repo_id = await store.upsert_repo(
        repo.full_name,
        license_spdx=repo.license_spdx,
        default_branch=repo.default_branch,
        html_url=repo.html_url,
    )
    return repo_id, repo


async def _store_issue_batch(
    batch: list[IssueRecord],
    *,
    repo: str,
    repo_id: int,
    store: Store,
    publisher: Publisher,
    backfill: bool,
    stats: SyncStats,
) -> None:
    results = await store.upsert_issues(repo_id, batch)
    by_number = {issue.number: issue for issue in batch}
    for result in results:
        issue = by_number[result.number]
        stats.issues += 1
        stats.new_issues += int(result.is_new)
        await publisher.publish(
            IssueEvent(
                type="issue.opened" if result.is_new else "issue.updated",
                repo=repo,
                number=issue.number,
                kind=issue.kind,
                title=issue.title,
                created_at=issue.created_at,
                updated_at=issue.updated_at,
                backfill=backfill,
            )
        )
        stats.events += 1


async def _sync_comments(
    gh: GitHubClient, store: Store, repo: str, repo_id: int, since: datetime, stats: SyncStats
) -> datetime | None:
    newest: datetime | None = None
    batch: list[CommentRecord] = []
    params: dict[str, str | int] = {"sort": "updated", "direction": "asc", "since": _iso(since)}
    async for raw in gh.paginate(f"/repos/{repo}/issues/comments", params):
        comment = CommentRecord.from_rest(GhComment.model_validate(raw))
        batch.append(comment)
        newest = max(newest or comment.updated_at, comment.updated_at)
        if len(batch) >= BATCH:
            stats.comments += await store.upsert_comments(repo_id, batch)
            batch = []
    if batch:
        stats.comments += await store.upsert_comments(repo_id, batch)
    return newest


async def backfill_repo(
    gh: GitHubClient,
    store: Store,
    publisher: Publisher,
    full_name: str,
    *,
    limit: int | None = None,
) -> SyncStats:
    """Fetch the newest `limit` issues + PRs (all if None) and their comments, then set cursors."""
    stats = SyncStats(full_name)
    repo_id, repo = await ensure_repo(gh, store, full_name)
    params: dict[str, str | int] = {"state": "all", "sort": "created", "direction": "desc"}
    batch: list[IssueRecord] = []
    newest: datetime | None = None
    oldest: datetime | None = None
    async for raw in gh.paginate(f"/repos/{repo.full_name}/issues", params, max_items=limit):
        issue = IssueRecord.from_rest(GhIssue.model_validate(raw))
        batch.append(issue)
        newest = max(newest or issue.updated_at, issue.updated_at)
        oldest = min(oldest or issue.created_at, issue.created_at)
        if len(batch) >= BATCH:
            await _store_issue_batch(
                batch,
                repo=repo.full_name,
                repo_id=repo_id,
                store=store,
                publisher=publisher,
                backfill=True,
                stats=stats,
            )
            batch = []
    if batch:
        await _store_issue_batch(
            batch,
            repo=repo.full_name,
            repo_id=repo_id,
            store=store,
            publisher=publisher,
            backfill=True,
            stats=stats,
        )
    if oldest is not None and newest is not None:
        await _sync_comments(gh, store, repo.full_name, repo_id, oldest, stats)
        await store.set_cursor(repo_id, "issues", newest)
        await store.set_cursor(repo_id, "comments", newest)
    await publisher.flush()
    log.info("backfill %s: %s", full_name, stats)
    return stats


async def incremental_repo(
    gh: GitHubClient, store: Store, publisher: Publisher, full_name: str
) -> SyncStats:
    """Fetch everything updated since the stored cursor. New issues emit `issue.opened`."""
    stats = SyncStats(full_name)
    repo_id, repo = await ensure_repo(gh, store, full_name)
    since = await store.get_cursor(repo_id, "issues")
    if since is None:
        raise RuntimeError(f"no cursor for {full_name}: run a backfill first")
    params: dict[str, str | int] = {
        "state": "all",
        "sort": "updated",
        "direction": "asc",
        "since": _iso(since),
    }
    batch: list[IssueRecord] = []
    newest = since
    async for raw in gh.paginate(f"/repos/{repo.full_name}/issues", params):
        issue = IssueRecord.from_rest(GhIssue.model_validate(raw))
        batch.append(issue)
        newest = max(newest, issue.updated_at)
        if len(batch) >= BATCH:
            await _store_issue_batch(
                batch,
                repo=repo.full_name,
                repo_id=repo_id,
                store=store,
                publisher=publisher,
                backfill=False,
                stats=stats,
            )
            batch = []
    if batch:
        await _store_issue_batch(
            batch,
            repo=repo.full_name,
            repo_id=repo_id,
            store=store,
            publisher=publisher,
            backfill=False,
            stats=stats,
        )
    comments_since = await store.get_cursor(repo_id, "comments") or since
    newest_comment = await _sync_comments(gh, store, repo.full_name, repo_id, comments_since, stats)
    await store.set_cursor(repo_id, "issues", newest)
    if newest_comment is not None:
        await store.set_cursor(repo_id, "comments", newest_comment)
    await publisher.flush()
    return stats


def doc_url(full_name: str, path: str) -> str:
    prefix, site = DOCS_SOURCES[full_name]
    rel = path.removeprefix(prefix).removesuffix(".md")
    if rel == "index":
        return site
    rel = rel.removesuffix("/index")
    return f"{site}{rel}/"


def doc_title(content: str, path: str) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.rsplit("/", 1)[-1].removesuffix(".md")


async def ingest_docs(
    gh: GitHubClient,
    store: Store,
    publisher: Publisher,
    full_name: str,
    *,
    limit: int | None = None,
) -> SyncStats:
    """Fetch markdown docs via the git trees + blobs APIs; skip blobs whose sha is unchanged."""
    stats = SyncStats(full_name)
    prefix, _ = DOCS_SOURCES[full_name]
    repo_id, repo = await ensure_repo(gh, store, full_name)
    tree = (
        await gh.get(f"/repos/{full_name}/git/trees/{repo.default_branch}", {"recursive": 1})
    ).data
    entries: list[dict[str, Any]] = [
        e
        for e in tree.get("tree", [])
        if e.get("type") == "blob"
        and str(e.get("path", "")).startswith(prefix)
        and str(e.get("path", "")).endswith(".md")
    ]
    entries.sort(key=lambda e: str(e["path"]))
    for entry in entries[:limit]:
        path, sha = str(entry["path"]), str(entry["sha"])
        if await store.doc_sha(repo_id, path) == sha:
            continue
        blob = (await gh.get(f"/repos/{full_name}/git/blobs/{sha}")).data
        content = base64.b64decode(blob["content"]).decode("utf-8", errors="replace")
        await store.upsert_doc(
            repo_id,
            DocRecord(
                path=path,
                url=doc_url(full_name, path),
                title=doc_title(content, path),
                content=content,
                sha=sha,
            ),
        )
        await publisher.publish(DocEvent(repo=full_name, path=path, sha=sha))
        stats.docs += 1
        stats.events += 1
    await publisher.flush()
    return stats


async def ingest_discussions(
    gh: GitHubClient,
    store: Store,
    publisher: Publisher,
    full_name: str,
    *,
    limit: int,
) -> SyncStats:
    """Discussions are only in GraphQL. Stored as `kind='discussion'` (they share issue numbers)."""
    stats = SyncStats(full_name)
    repo_id, _ = await ensure_repo(gh, store, full_name)
    owner, name = full_name.split("/")
    after: str | None = None
    fetched = 0
    while fetched < limit:
        data = await gh.graphql(
            DISCUSSIONS_QUERY,
            {"owner": owner, "name": name, "first": min(50, limit - fetched), "after": after},
        )
        conn = data["repository"]["discussions"]
        issues: list[IssueRecord] = []
        comments: list[CommentRecord] = []
        for node in conn["nodes"]:
            issue, node_comments = _discussion_records(node)
            issues.append(issue)
            comments.extend(node_comments)
        await _store_issue_batch(
            issues,
            repo=full_name,
            repo_id=repo_id,
            store=store,
            publisher=publisher,
            backfill=True,
            stats=stats,
        )
        stats.comments += await store.upsert_comments(repo_id, comments)
        fetched += len(issues)
        if not conn["pageInfo"]["hasNextPage"] or not issues:
            break
        after = conn["pageInfo"]["endCursor"]
    await publisher.flush()
    return stats


def _discussion_records(node: dict[str, Any]) -> tuple[IssueRecord, list[CommentRecord]]:
    issue = IssueRecord(
        number=node["number"],
        kind="discussion",
        title=node["title"],
        body=node.get("body") or "",
        state="closed" if node.get("closed") else "open",
        state_reason=None,
        author=(node.get("author") or {}).get("login"),
        labels=[label["name"] for label in node.get("labels", {}).get("nodes", [])],
        html_url=node["url"],
        created_at=node["createdAt"],
        updated_at=node["updatedAt"],
        closed_at=node.get("closedAt"),
        comments_count=node.get("comments", {}).get("totalCount", 0),
    )
    comments = [
        CommentRecord(
            id=c["databaseId"],
            issue_number=issue.number,
            author=(c.get("author") or {}).get("login"),
            author_association=c.get("authorAssociation"),
            body=c.get("body") or "",
            html_url=c["url"],
            created_at=c["createdAt"],
            updated_at=c["updatedAt"],
        )
        for c in node.get("comments", {}).get("nodes", [])
        if c.get("databaseId") is not None
    ]
    return issue, comments


def _iso(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")
