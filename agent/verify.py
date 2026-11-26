"""Deterministic critic: citations exist, quotes appear in their source, labels are allowed."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent.schemas import Classification, Draft
from indexing.embeddings import tokens

_INLINE_CITE = re.compile(r"\[(S\d+)\]")
_WS = re.compile(r"\s+")
QUOTE_TOKEN_COVERAGE = 0.9


@dataclass
class Verification:
    ok: bool
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


def quote_in_source(quote: str, source: str) -> bool:
    """Exact (whitespace/case-insensitive) match, or 90% of the quote's tokens in the source.

    The token fallback tolerates markdown stripping and small copy errors of 7-8B models without
    accepting paraphrases.
    """
    q, s = _norm(quote), _norm(source)
    if not q:
        return False
    if q in s:
        return True
    q_tokens = tokens(q)
    if len(q_tokens) < 4:
        return False
    s_tokens = set(tokens(s))
    return sum(1 for t in q_tokens if t in s_tokens) / len(q_tokens) >= QUOTE_TOKEN_COVERAGE


def verify_draft(draft: Draft, evidence: dict[str, str]) -> list[str]:
    problems: list[str] = []
    if draft.can_answer and not draft.claims:
        problems.append("can_answer is true but there are no claims")
    if not draft.can_answer and draft.claims:
        problems.append("refusal must not contain claims")
    for i, claim in enumerate(draft.claims):
        source = evidence.get(claim.source_id)
        if source is None:
            problems.append(f"claim {i} cites unknown source {claim.source_id}")
        elif not quote_in_source(claim.quote, source):
            problems.append(f"claim {i}: quote not found in {claim.source_id}")
    for cited in set(_INLINE_CITE.findall(draft.reply_markdown)):
        if cited not in evidence:
            problems.append(f"reply cites {cited}, which is not an evidence item")
    claimed = {c.source_id for c in draft.claims}
    for cited in set(_INLINE_CITE.findall(draft.reply_markdown)) - claimed:
        if cited in evidence:
            problems.append(f"reply cites {cited} without a supporting claim")
    return problems


def verify_classification(
    classification: Classification, allowed_labels: list[str], areas: list[str]
) -> list[str]:
    problems: list[str] = []
    bad = [label for label in classification.labels if label not in allowed_labels]
    if bad:
        problems.append(f"labels not in the allowed set: {bad}")
    if classification.area not in areas:
        problems.append(f"area {classification.area!r} is not allowed")
    return problems
