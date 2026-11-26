"""Ground truth mined from history: duplicates, maintainer labels, linked answers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from ingestion.models import CommentRecord, IssueRecord

# GitHub's own convention: a comment "Duplicate of #123" marks the issue as a duplicate.
_DUPLICATE_OF = re.compile(r"(?im)^\s*duplicate of\s+#(\d+)\b")
_LINK = re.compile(
    r"https://(?:fastapi\.tiangolo\.com|docs\.pydantic\.dev|docs\.astral\.sh/uv)[^\s)\]>\"']*"
    r"|https://github\.com/[\w.-]+/[\w.-]+/(?:pull|issues)/\d+"
)
MAINTAINERS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


@dataclass(frozen=True)
class DuplicateCase:
    repo: str
    number: int
    target: int
    title: str
    body: str
    created_at: datetime


def find_duplicate_target(issue: IssueRecord, comments: list[CommentRecord]) -> int | None:
    for comment in comments:
        match = _DUPLICATE_OF.search(comment.body)
        if match and int(match.group(1)) != issue.number:
            return int(match.group(1))
    return None


def duplicate_cases(
    repo: str,
    threads: list[tuple[IssueRecord, list[CommentRecord]]],
) -> list[DuplicateCase]:
    """Cases whose target exists in the corpus and was opened before the duplicate."""
    created = {issue.number: issue.created_at for issue, _ in threads}
    cases: list[DuplicateCase] = []
    for issue, comments in threads:
        target = find_duplicate_target(issue, comments)
        if target is None or target not in created or created[target] >= issue.created_at:
            continue
        cases.append(
            DuplicateCase(repo, issue.number, target, issue.title, issue.body, issue.created_at)
        )
    return cases


def linked_answers(comments: list[CommentRecord]) -> list[str]:
    """Doc pages / PRs / issues linked by maintainers: evidence a good answer should cite."""
    links: list[str] = []
    for comment in comments:
        if comment.author_association not in MAINTAINERS:
            continue
        for link in _LINK.findall(comment.body):
            normalized = link.rstrip(".,;:").split("#")[0]
            if normalized not in links:
                links.append(normalized)
    return links
