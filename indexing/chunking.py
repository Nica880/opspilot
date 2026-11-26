"""Split issues, comments and docs into retrieval chunks with a stable id and payload."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ingestion.models import CommentRecord, DocRecord, IssueRecord

NAMESPACE = uuid.UUID("6f0b6f6e-7370-696c-6f74-2d6368756e6b")
MAX_CHARS = 1500
OVERLAP = 200
MAX_COMMENT_CHUNKS = 20
MAINTAINER_ROLES = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_BLANK_RUNS = re.compile(r"\n{3,}")
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    payload: dict[str, Any]


def clean(text: str) -> str:
    """Drop HTML comments (issue templates are full of them) and collapse blank runs."""
    return _BLANK_RUNS.sub("\n\n", _HTML_COMMENT.sub("", text)).strip()


def split_text(text: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP) -> list[str]:
    """Paragraph-aware split; paragraphs longer than `max_chars` are hard-split with overlap."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            parts.append(current)
        while len(para) > max_chars:
            parts.append(para[:max_chars])
            para = para[max_chars - overlap :]
        current = para
    if current:
        parts.append(current)
    return parts


def doc_key_issue(repo: str, number: int) -> str:
    return f"{repo}#{number}"


def doc_key_page(repo: str, path: str) -> str:
    return f"{repo}:{path}"


def _chunk_id(doc_key: str, section: str, index: int) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{doc_key}|{section}|{index}"))


def _ts(value: datetime) -> float:
    return value.timestamp()


def issue_chunks(repo: str, issue: IssueRecord, comments: list[CommentRecord]) -> list[Chunk]:
    key = doc_key_issue(repo, issue.number)
    base: dict[str, Any] = {
        "doc_key": key,
        "repo": repo,
        "kind": issue.kind,
        "number": issue.number,
        "title": issue.title,
        "state": issue.state,
        "labels": issue.labels,
        "created_at": issue.created_at.isoformat(),
        "created_ts": _ts(issue.created_at),
    }
    chunks: list[Chunk] = []
    for i, part in enumerate(split_text(f"{issue.title}\n\n{clean(issue.body)}")):
        chunks.append(
            Chunk(
                _chunk_id(key, "body", i), part, {**base, "section": "body", "url": issue.html_url}
            )
        )
    # Maintainer answers first: they are the most useful evidence when the cap kicks in.
    ordered = sorted(
        comments,
        key=lambda c: (c.author_association not in MAINTAINER_ROLES, c.created_at),
    )
    count = 0
    for comment in ordered:
        for i, part in enumerate(split_text(clean(comment.body))):
            if count >= MAX_COMMENT_CHUNKS:
                return chunks
            chunks.append(
                Chunk(
                    _chunk_id(key, f"comment-{comment.id}", i),
                    f"{issue.title}\n\n{part}",
                    {
                        **base,
                        "section": "comment",
                        "comment_id": comment.id,
                        "maintainer": comment.author_association in MAINTAINER_ROLES,
                        "url": comment.html_url,
                    },
                )
            )
            count += 1
    return chunks


def slugify(heading: str) -> str:
    """mkdocs-style anchor: lowercase, punctuation dropped, spaces to dashes."""
    slug = re.sub(r"[^\w\- ]", "", heading.lower()).strip()
    return re.sub(r"[\s]+", "-", slug)


def doc_chunks(repo: str, doc: DocRecord) -> list[Chunk]:
    key = doc_key_page(repo, doc.path)
    text = clean(doc.content)
    sections: list[tuple[str, str]] = []
    matches = list(_HEADING.finditer(text))
    if not matches or matches[0].start() > 0:
        end = matches[0].start() if matches else len(text)
        sections.append(("", text[:end]))
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        sections.append((match.group(2), text[match.start() : end]))
    chunks: list[Chunk] = []
    for s_idx, (heading, body) in enumerate(sections):
        anchor = f"#{slugify(heading)}" if heading and s_idx > 0 else ""
        for i, part in enumerate(split_text(body)):
            chunks.append(
                Chunk(
                    _chunk_id(key, f"s{s_idx}", i),
                    f"{doc.title}\n\n{part}" if s_idx > 0 else part,
                    {
                        "doc_key": key,
                        "repo": repo,
                        "kind": "doc",
                        "path": doc.path,
                        "title": doc.title,
                        "heading": heading,
                        "section": "doc",
                        "url": f"{doc.url}{anchor}",
                    },
                )
            )
    return chunks
