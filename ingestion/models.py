"""GitHub payload models (only the fields we use) and the events we publish."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

IssueKind = Literal["issue", "pull_request", "discussion"]

_ISSUE_URL_NUMBER = re.compile(r"/issues/(\d+)$")


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class GhUser(_Lenient):
    login: str


class GhLabel(_Lenient):
    name: str


class GhLicense(_Lenient):
    spdx_id: str | None = None


class GhRepo(_Lenient):
    full_name: str
    html_url: str
    default_branch: str
    license: GhLicense | None = None

    @property
    def license_spdx(self) -> str | None:
        if self.license is None or self.license.spdx_id in (None, "NOASSERTION"):
            return None
        return self.license.spdx_id


class GhIssue(_Lenient):
    number: int
    title: str
    body: str | None = None
    state: str
    state_reason: str | None = None
    user: GhUser | None = None
    labels: list[GhLabel] = Field(default_factory=list)
    html_url: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    comments: int = 0
    pull_request: dict[str, Any] | None = None

    @property
    def kind(self) -> IssueKind:
        return "pull_request" if self.pull_request is not None else "issue"


class GhComment(_Lenient):
    id: int
    issue_url: str
    user: GhUser | None = None
    author_association: str | None = None
    body: str | None = None
    html_url: str
    created_at: datetime
    updated_at: datetime

    @property
    def issue_number(self) -> int:
        match = _ISSUE_URL_NUMBER.search(self.issue_url)
        if match is None:
            raise ValueError(f"unexpected issue_url: {self.issue_url}")
        return int(match.group(1))


class IssueRecord(BaseModel):
    """Normalised issue, PR or discussion, as stored in Postgres."""

    number: int
    kind: IssueKind
    title: str
    body: str
    state: str
    state_reason: str | None
    author: str | None
    labels: list[str]
    html_url: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    comments_count: int

    @classmethod
    def from_rest(cls, issue: GhIssue) -> IssueRecord:
        return cls(
            number=issue.number,
            kind=issue.kind,
            title=issue.title,
            body=issue.body or "",
            state=issue.state,
            state_reason=issue.state_reason,
            author=issue.user.login if issue.user else None,
            labels=[label.name for label in issue.labels],
            html_url=issue.html_url,
            created_at=issue.created_at,
            updated_at=issue.updated_at,
            closed_at=issue.closed_at,
            comments_count=issue.comments,
        )


class CommentRecord(BaseModel):
    id: int
    issue_number: int
    author: str | None
    author_association: str | None
    body: str
    html_url: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_rest(cls, comment: GhComment) -> CommentRecord:
        return cls(
            id=comment.id,
            issue_number=comment.issue_number,
            author=comment.user.login if comment.user else None,
            author_association=comment.author_association,
            body=comment.body or "",
            html_url=comment.html_url,
            created_at=comment.created_at,
            updated_at=comment.updated_at,
        )


class DocRecord(BaseModel):
    path: str
    url: str
    title: str
    content: str
    sha: str


class IssueEvent(BaseModel):
    """Published to the `issues.v1` topic, keyed by `repo#number`."""

    type: Literal["issue.opened", "issue.updated"]
    repo: str
    number: int
    kind: IssueKind
    title: str
    created_at: datetime
    updated_at: datetime
    backfill: bool

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"


class DocEvent(BaseModel):
    """Published to the `docs.v1` topic when a docs page is new or changed."""

    type: Literal["doc.upserted"] = "doc.upserted"
    repo: str
    path: str
    sha: str

    @property
    def key(self) -> str:
        return f"{self.repo}:{self.path}"
