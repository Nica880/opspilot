"""Temporal activities. They build the runtime lazily, once per worker process."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from temporalio import activity

from agent.runtime import Runtime, build_runtime
from agent.service import IssueInput
from common.config import get_settings
from common.db import get_pool
from workflows.triage import ResumeInput, TriageInput, TriageResult, workflow_id

_runtime: Runtime | None = None
_lock = asyncio.Lock()


async def runtime() -> Runtime:
    global _runtime
    async with _lock:
        if _runtime is None:
            _runtime = await build_runtime(get_settings())
    return _runtime


@activity.defn(name="run_triage")
async def run_triage(inp: TriageInput) -> str:
    rt = await runtime()
    record = await rt.service.start(
        IssueInput(inp.repo, inp.number, inp.title, inp.body, inp.url, inp.created_at),
        run_id=inp.run_id,
        workflow_id=workflow_id(inp.repo, inp.number),
    )
    return record.status


@activity.defn(name="resume_triage")
async def resume_triage(inp: ResumeInput) -> TriageResult:
    rt = await runtime()
    out = await rt.service.resume(inp.run_id, asdict(inp.decision))
    return TriageResult(inp.run_id, out["status"], out["applied"])


@activity.defn(name="expire_run")
async def expire_run(run_id: str) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE triage_runs SET status = 'expired', finished_at = now() WHERE id = %s",
            (run_id,),
        )


ACTIVITIES: list[Callable[..., Any]] = [run_triage, resume_triage, expire_run]
