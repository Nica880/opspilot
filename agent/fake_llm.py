"""Deterministic fake LLM for CI and offline demos. Same interface as OllamaProvider.

It reads the same prompts the real model gets and answers with simple, explainable heuristics.
It never follows instructions inside untrusted blocks (it does not "follow" anything at all), so
red-team results with the fake measure the guardrails and the graph, not model obedience.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent.llm import Message
from indexing.embeddings import tokens

_UNTRUSTED = re.compile(r'<untrusted kind="([^"]+)">\n(.*?)\n</untrusted>', re.DOTALL)
_EVIDENCE_ID = re.compile(r"^\[(S\d+)\]", re.M)
_AREAS = re.compile(r"exactly one of: ([^\n(]+)")
_LABELS = re.compile(r"(?:ONLY from this list|copied exactly from): ([^\n]*)")
_CLAIM = re.compile(r"^(\d+)\. quote: (.*)\n\s*statement: (.*)$", re.M)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def _split_list(raw: str) -> list[str]:
    raw = raw.strip().removesuffix(". Do not invent labels.").rstrip(".")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text) if len(s.strip()) > 12]


def _overlap(a: str, b: str) -> float:
    ta, tb = set(tokens(a)), set(tokens(b))
    return len(ta & tb) / len(ta) if ta else 0.0


def _jaccard(a: str, b: str) -> float:
    ta, tb = set(tokens(a)), set(tokens(b))
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


class FakeProvider:
    name = "fake"

    def __init__(self, broken_first: int = 0) -> None:
        self._broken_left = broken_first
        self.calls: list[str] = []

    async def complete(self, messages: list[Message], schema: dict[str, Any], *, task: str) -> str:
        self.calls.append(task)
        if self._broken_left > 0:
            self._broken_left -= 1
            return '{"not": "valid for the schema"'
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user = "\n".join(m["content"] for m in messages if m["role"] == "user")
        blocks = _UNTRUSTED.findall(user)
        handler = {
            "classify": self._classify,
            "dedupe": self._dedupe,
            "draft": self._draft,
            "critic": self._critic,
        }[task]
        return json.dumps(handler(system, user, blocks))

    @staticmethod
    def _issue(blocks: list[tuple[str, str]]) -> str:
        return next((text for kind, text in blocks if kind == "issue"), "")

    def _classify(self, system: str, _: str, blocks: list[tuple[str, str]]) -> dict[str, Any]:
        text = self._issue(blocks)
        low = text.lower()
        title = text.split("\n", 1)[0].lower()
        if any(w in low for w in ("documentation", "typo", "docs page", "the docs ")):
            kind = "docs"
        elif any(
            w in low
            for w in (
                "feature request",
                "would be nice",
                "support for",
                "add an option",
                "please add",
            )
        ):
            kind = "feature"
        elif any(
            w in low
            for w in ("traceback", "error", "exception", "crash", "bug", "regression", "fails")
        ):
            kind = "bug"
        else:
            kind = "question" if ("?" in title or "how" in title) else "bug"
        if any(w in low for w in ("security", "vulnerab", "data loss", "corrupt")):
            severity = "critical"
        elif any(w in low for w in ("crash", "segfault", "hang", "regression", "panic")):
            severity = "high"
        else:
            severity = "medium" if kind == "bug" else "low"
        areas_match = _AREAS.search(system)
        areas = _split_list(areas_match.group(1)) if areas_match else ["other"]
        area = next((a for a in areas if a != "other" and a.replace("-", " ") in low), "other")
        labels_match = _LABELS.search(system)
        allowed = _split_list(labels_match.group(1)) if labels_match else []
        wanted = {
            "bug": ("bug", "bug v2"),
            "feature": ("feature", "enhancement", "feature request"),
            "question": ("question",),
            "docs": ("docs", "documentation"),
        }[kind]
        labels = [label for label in allowed if label.lower() in wanted]
        labels += [lab for lab in allowed if lab.lower() == area and lab not in labels]
        first = next(iter(_sentences(text)), text[:80])
        return {
            "evidence_quotes": [first[:200]] if first else [],
            "reasoning": f"Heuristic: keywords suggest {kind}.",
            "type": kind,
            "area": area,
            "severity": severity,
            "labels": labels[:5],
            "confidence": 0.55,
        }

    def _dedupe(self, _: str, __: str, blocks: list[tuple[str, str]]) -> dict[str, Any]:
        texts = {kind: text for kind, text in blocks}
        score = _jaccard(texts.get("new_issue", ""), texts.get("existing_issue", ""))
        return {
            "shared_symptoms": "token overlap" if score > 0 else "none",
            "differences": "none" if score > 0.35 else "different wording",
            "is_duplicate": score > 0.35,
            "confidence": round(min(1.0, score * 2), 2),
        }

    def _draft(self, _: str, user: str, blocks: list[tuple[str, str]]) -> dict[str, Any]:
        issue = self._issue(blocks)
        sources = [(kind, text) for kind, text in blocks if kind.startswith("S")]
        best: tuple[float, str, str] | None = None
        for sid, text in sources:
            for sentence in _sentences(text):
                score = _overlap(issue, sentence)
                if best is None or score > best[0]:
                    best = (score, sid, sentence)
        checklist = []
        if "```" not in issue:
            checklist.append("A minimal, self-contained code example that reproduces the problem")
        if "version" not in issue.lower():
            checklist.append("Library, Python and OS versions")
        if "traceback" not in issue.lower():
            checklist.append("The full traceback, if there is one")
        if best is None or best[0] < 0.2:
            return {
                "claims": [],
                "can_answer": False,
                "refusal_reason": "No evidence item addresses this issue.",
                "reply_markdown": (
                    "Thanks for the report! Could you share a minimal example "
                    "so we can reproduce it?"
                ),
                "repro_checklist": checklist,
            }
        _score, sid, sentence = best
        return {
            "claims": [{"source_id": sid, "quote": sentence, "statement": sentence}],
            "can_answer": True,
            "refusal_reason": "",
            "reply_markdown": f"Thanks for the report. This looks related to: {sentence} [{sid}]",
            "repro_checklist": checklist,
        }

    def _critic(self, _: str, user: str, __: list[tuple[str, str]]) -> dict[str, Any]:
        bad = [
            int(idx)
            for idx, quote, statement in _CLAIM.findall(user)
            if _overlap(statement, quote) < 0.5
        ]
        return {"unsupported_claims": bad, "supported": not bad}

    @staticmethod
    def evidence_ids(user: str) -> list[str]:
        return _EVIDENCE_ID.findall(user)
