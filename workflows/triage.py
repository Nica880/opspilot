"""Temporal workflow: one per issue. Triage runs as an activity up to the LangGraph interrupt,
then the workflow durably waits (survives restarts) for a human decision signal."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, cast

from temporalio import workflow
from temporalio.common import RetryPolicy

APPROVAL_TIMEOUT = timedelta(days=7)
TRIAGE_TIMEOUT = timedelta(minutes=20)
RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=10), backoff_coefficient=2.0, maximum_attempts=3
)


@dataclass
class TriageInput:
    run_id: str
    repo: str
    number: int
    title: str
    body: str
    url: str
    created_at: str


@dataclass
class Decision:
    decision: str  # approve | edit | reject
    reviewer: str
    edited: dict[str, Any] | None = None
    note: str | None = None


@dataclass
class ResumeInput:
    run_id: str
    decision: Decision


@dataclass
class TriageResult:
    run_id: str
    status: str
    applied: dict[str, Any] | None = field(default=None)


def workflow_id(repo: str, number: int) -> str:
    return f"triage-{repo.replace('/', '-')}-{number}"


@workflow.defn
class TriageWorkflow:
    def __init__(self) -> None:
        self._decision: Decision | None = None
        self._status = "running"

    @workflow.run
    async def run(self, inp: TriageInput) -> TriageResult:
        self._status = await workflow.execute_activity(
            "run_triage",
            inp,
            start_to_close_timeout=TRIAGE_TIMEOUT,
            retry_policy=RETRY,
            result_type=str,
        )
        try:
            await workflow.wait_condition(
                lambda: self._decision is not None, timeout=APPROVAL_TIMEOUT
            )
        except TimeoutError:
            await workflow.execute_activity(
                "expire_run", inp.run_id, start_to_close_timeout=timedelta(minutes=1)
            )
            self._status = "expired"
            return TriageResult(inp.run_id, "expired")
        assert self._decision is not None
        self._status = "applying"
        result: TriageResult = cast(
            TriageResult,
            await workflow.execute_activity(
                "resume_triage",
                ResumeInput(inp.run_id, self._decision),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RETRY,
                result_type=TriageResult,
            ),
        )
        self._status = result.status
        return result

    @workflow.signal
    def decide(self, decision: Decision) -> None:
        if self._decision is None and decision.decision in ("approve", "edit", "reject"):
            self._decision = decision

    @workflow.query
    def status(self) -> str:
        return self._status
