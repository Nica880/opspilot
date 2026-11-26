"""promptfoo Python provider: runs the real triage graph (fake LLM in CI, Ollama locally).

The prompt rendered by promptfoo is "<title>\n\n<body>". Output is a JSON summary of the run.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langgraph.checkpoint.memory import InMemorySaver

from agent.graph import TriageDeps, build_graph
from agent.llm import LLMProvider, OllamaProvider, StructuredLLM
from agent.runs import MemoryRunStore
from agent.service import IssueInput, TriageConfig, TriageService
from agent.taxonomy import labels_for
from evals.redteam.run import REPO, redteam_search


def _provider() -> LLMProvider:
    if os.environ.get("LLM_PROVIDER", "fake") == "fake":
        from agent.fake_llm import FakeProvider

        return FakeProvider()
    return OllamaProvider(
        os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        os.environ.get("LLM_MODEL", "qwen2.5:7b-instruct"),
    )


async def _run(prompt: str, prompt_version: str) -> dict[str, Any]:
    title, _, body = prompt.partition("\n\n")
    allowed = labels_for(REPO)
    deps = TriageDeps(llm=StructuredLLM(_provider()), search=await redteam_search())
    service = TriageService(
        build_graph(deps, InMemorySaver()),
        MemoryRunStore(),
        TriageConfig("promptfoo", prompt_version),
        labels_by_repo={REPO: allowed},
    )
    record = await service.start(
        IssueInput(
            REPO,
            99999,
            title,
            body,
            "https://example.invalid",
            datetime(2025, 6, 1, tzinfo=UTC).isoformat(),
        ),
        run_id="promptfoo",
    )
    s = record.suggestions
    classification = s.get("classification") or {}
    draft = s.get("draft") or {}
    return {
        "type": classification.get("type"),
        "area": classification.get("area"),
        "severity": classification.get("severity"),
        "labels": s.get("labels") or [],
        "allowed_labels": allowed,
        "injection_flags": s.get("injection_flags") or [],
        "can_answer": draft.get("can_answer"),
        "reply": draft.get("reply_markdown", ""),
        "verification_ok": (s.get("verification") or {}).get("ok"),
        "status": record.status,
    }


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    version = str((options.get("config") or {}).get("prompt_version", "v1"))
    return {"output": json.dumps(asyncio.run(_run(prompt, version)))}
