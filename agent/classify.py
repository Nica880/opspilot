"""Classification step shared by the graph, the MCP server and the A2A endpoint."""

from __future__ import annotations

from typing import Any

from agent.guardrails import wrap_untrusted
from agent.llm import Message, StructuredLLM
from agent.prompts import load_prompt
from agent.schemas import Classification


async def classify_issue(
    llm: StructuredLLM,
    *,
    repo: str,
    title: str,
    sanitized_body: str,
    prompt_version: str,
    areas: list[str],
    allowed_labels: list[str],
) -> dict[str, Any]:
    """Return the classification clamped to the allowed vocabularies.

    Raises StructuredOutputError when the model cannot produce valid JSON within the repair budget.
    """
    system = load_prompt(
        prompt_version,
        "classify",
        repo=repo,
        areas=", ".join(areas),
        labels=", ".join(allowed_labels),
    )
    user = wrap_untrusted("issue", f"Title: {title}\n\n{sanitized_body}")
    result = await llm.generate(
        "classify", [Message.of("system", system), Message.of("user", user)], Classification
    )
    data = result.model_dump()
    data["proposed_labels_raw"] = list(result.labels)
    data["labels"] = [label for label in result.labels if label in allowed_labels]
    if result.area not in areas:
        data["area"] = "other"
    return data
