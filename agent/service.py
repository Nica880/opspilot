"""Run the graph up to the approval interrupt, and resume it with a human decision."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from agent.graph import TriageState
from agent.runs import RunRecord, RunStore, suggestions_from_state
from agent.taxonomy import areas_for, labels_for


@dataclass(frozen=True)
class IssueInput:
    repo: str
    number: int
    title: str
    body: str
    url: str
    created_at: str
    knowledge_cutoff: str | None = None


@dataclass(frozen=True)
class TriageConfig:
    model: str
    prompt_version: str = "v1"
    retrieval_variant: str = "hybrid_rerank"


def new_run_id() -> str:
    return str(uuid.uuid4())


def _config(run_id: str) -> RunnableConfig:
    return RunnableConfig(configurable={"thread_id": run_id})


class TriageService:
    def __init__(
        self,
        graph: CompiledStateGraph[TriageState, None, TriageState, TriageState],
        runs: RunStore,
        config: TriageConfig,
        labels_by_repo: dict[str, list[str]] | None = None,
    ) -> None:
        self.graph = graph
        self.runs = runs
        self.config = config
        self.labels_by_repo = labels_by_repo or {}

    async def start(
        self, issue: IssueInput, *, run_id: str | None = None, workflow_id: str | None = None
    ) -> RunRecord:
        """Idempotent per run_id: re-running resumes from the last checkpoint."""
        run_id = run_id or new_run_id()
        existing = await self.runs.get(run_id)
        if existing is None:
            await self.runs.create(
                RunRecord(
                    run_id=run_id,
                    repo=issue.repo,
                    number=issue.number,
                    title=issue.title,
                    model=self.config.model,
                    prompt_version=self.config.prompt_version,
                    retrieval_variant=self.config.retrieval_variant,
                    status="running",
                    workflow_id=workflow_id,
                )
            )
        state: TriageState = {
            "run_id": run_id,
            "repo": issue.repo,
            "number": issue.number,
            "title": issue.title,
            "body": issue.body,
            "url": issue.url,
            "created_at": issue.created_at,
            "prompt_version": self.config.prompt_version,
            "retrieval_variant": self.config.retrieval_variant,
            "allowed_labels": labels_for(issue.repo, self.labels_by_repo.get(issue.repo)),
            "areas": areas_for(issue.repo),
        }
        if issue.knowledge_cutoff:
            state["knowledge_cutoff"] = issue.knowledge_cutoff
        started = time.perf_counter()
        try:
            snapshot = await self.graph.aget_state(_config(run_id))
            payload: TriageState | None = None if snapshot.next else state
            result: dict[str, Any] = await self.graph.ainvoke(cast(Any, payload), _config(run_id))
        except Exception as exc:
            await self.runs.fail(run_id, f"{type(exc).__name__}: {exc}")
            raise
        latency = int((time.perf_counter() - started) * 1000)
        await self.runs.awaiting_approval(
            run_id,
            steps=list(result.get("steps", [])),
            suggestions=suggestions_from_state(result),
            latency_ms=latency,
            needs_human=bool(result.get("needs_human")),
        )
        record = await self.runs.get(run_id)
        assert record is not None
        return record

    async def resume(self, run_id: str, decision: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = await self.graph.ainvoke(
            cast(Any, Command(resume=decision)), _config(run_id)
        )
        applied = result.get("applied") or {}
        status = str(
            result.get("status") or ("rejected" if decision["decision"] == "reject" else "approved")
        )
        await self.runs.decide(
            run_id, decision=decision, status=status, applied_url=applied.get("comment_url")
        )
        return {"status": status, "applied": applied or None}
