"""Triage runs and the approval queue."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from agent.runs import RunRecord, RunStore

router = APIRouter(prefix="/api", tags=["runs"])


class RunOut(BaseModel):
    run_id: str
    repo: str
    number: int
    title: str
    status: str
    model: str
    prompt_version: str
    retrieval_variant: str
    latency_ms: int | None
    started_at: str
    steps: list[dict[str, Any]]
    suggestions: dict[str, Any]
    decision: dict[str, Any] | None
    error: str | None

    @classmethod
    def of(cls, run: RunRecord) -> RunOut:
        return cls(
            run_id=run.run_id,
            repo=run.repo,
            number=run.number,
            title=run.title,
            status=run.status,
            model=run.model,
            prompt_version=run.prompt_version,
            retrieval_variant=run.retrieval_variant,
            latency_ms=run.latency_ms,
            started_at=run.started_at.isoformat(),
            steps=run.steps,
            suggestions=run.suggestions,
            decision=run.decision,
            error=run.error,
        )


class DecisionIn(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    reviewer: str = Field(min_length=1, max_length=80)
    edited: dict[str, Any] | None = None
    note: str | None = Field(default=None, max_length=2000)


class TriageRequest(BaseModel):
    repo: str
    number: int


async def get_runs(request: Request) -> RunStore:
    store: RunStore | None = getattr(request.app.state, "runs", None)
    if store is None:
        from agent.runs import PgRunStore
        from common.db import get_pool

        store = PgRunStore(await get_pool())
        request.app.state.runs = store
    return store


async def get_temporal(request: Request) -> Any:
    client = getattr(request.app.state, "temporal", None)
    if client is None:
        from temporalio.client import Client

        from common.config import get_settings

        settings = get_settings()
        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        request.app.state.temporal = client
    return client


Runs = Annotated[RunStore, Depends(get_runs)]


@router.get("/runs", response_model=list[RunOut])
async def list_runs(runs: Runs, status: str | None = None, limit: int = 50) -> list[RunOut]:
    return [RunOut.of(r) for r in await runs.recent(status=status, limit=min(limit, 200))]


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: str, runs: Runs) -> RunOut:
    run = await runs.get(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return RunOut.of(run)


@router.post("/runs/{run_id}/decision", status_code=202)
async def decide(
    run_id: str, body: DecisionIn, runs: Runs, temporal: Annotated[Any, Depends(get_temporal)]
) -> dict[str, str]:
    from workflows.client import send_decision
    from workflows.triage import Decision

    run = await runs.get(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if run.status not in ("awaiting_approval", "needs_human"):
        raise HTTPException(409, f"run is {run.status}, not awaiting a decision")
    if body.decision == "edit" and not body.edited:
        raise HTTPException(422, "an edit needs an edited payload")
    await send_decision(
        temporal,
        run.repo,
        run.number,
        Decision(body.decision, body.reviewer, body.edited, body.note),
    )
    return {"status": "accepted"}


@router.post("/triage", status_code=202)
async def triage(
    body: TriageRequest, temporal: Annotated[Any, Depends(get_temporal)]
) -> dict[str, str]:
    """Manually (re)start triage for an ingested issue: used by the demo and the inbox."""
    from common.config import get_settings
    from common.db import get_pool
    from indexing.corpus import CorpusReader
    from workflows.client import start_triage

    found = await CorpusReader(await get_pool()).issue(body.repo, body.number)
    if found is None:
        raise HTTPException(404, "issue not ingested")
    run_id = await start_triage(temporal, get_settings().temporal_task_queue, body.repo, found[0])
    return {"run_id": run_id}
