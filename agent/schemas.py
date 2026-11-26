"""Structured outputs. Field order matters: evidence/quote fields come BEFORE the values they
support and every key is required (no defaults), because 7-8B models fill values first and then
rationalise. JSON schemas are generated from these models and passed to Ollama's `format`."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IssueType = Literal["bug", "feature", "question", "docs"]
Severity = Literal["low", "medium", "high", "critical"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Classification(_Strict):
    evidence_quotes: list[str] = Field(
        description="1-3 short verbatim quotes from the issue that justify the decision",
        max_length=3,
    )
    reasoning: str = Field(description="One or two sentences.")
    type: IssueType
    area: str = Field(description="One of the allowed areas.")
    severity: Severity
    labels: list[str] = Field(description="Subset of the allowed labels.", max_length=5)
    confidence: float = Field(ge=0.0, le=1.0)


class DuplicateVerdict(_Strict):
    shared_symptoms: str = Field(description="What the two reports have in common, or 'none'.")
    differences: str = Field(description="What differs, or 'none'.")
    is_duplicate: bool
    confidence: float = Field(ge=0.0, le=1.0)


class Claim(_Strict):
    source_id: str = Field(description="Id of the evidence item, e.g. 'S2'.")
    quote: str = Field(description="Verbatim sentence copied from that source.")
    statement: str = Field(description="What the reply says, supported by the quote.")


class Draft(_Strict):
    claims: list[Claim] = Field(max_length=6)
    can_answer: bool = Field(description="False when the evidence does not answer the issue.")
    refusal_reason: str = Field(description="Why no grounded answer is possible, or ''.")
    reply_markdown: str = Field(description="The reply; cite sources inline as [S1], [S2].")
    repro_checklist: list[str] = Field(
        description="Concrete steps/info the reporter should provide to reproduce.", max_length=8
    )


class CriticVerdict(_Strict):
    unsupported_claims: list[int] = Field(
        description="Indexes of claims the quote does not support."
    )
    supported: bool
