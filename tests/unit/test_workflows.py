from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from temporalio import activity
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from agent.runs import MemoryRunStore, RunRecord
from api.main import create_app
from ingestion.models import IssueEvent, IssueRecord
from workflows.client import run_id_for, start_triage
from workflows.consumers import make_triage_handler, should_triage
from workflows.triage import (
    Decision,
    ResumeInput,
    TriageInput,
    TriageResult,
    TriageWorkflow,
    workflow_id,
)

QUEUE = "test-triage"


class Calls:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.resumed: list[ResumeInput] = []
        self.expired: list[str] = []


def activities(calls: Calls) -> list[Any]:
    @activity.defn(name="run_triage")
    async def run_triage(inp: TriageInput) -> str:
        calls.started.append(inp.run_id)
        return "awaiting_approval"

    @activity.defn(name="resume_triage")
    async def resume_triage(inp: ResumeInput) -> TriageResult:
        calls.resumed.append(inp)
        status = "rejected" if inp.decision.decision == "reject" else "applied"
        return TriageResult(inp.run_id, status, None)

    @activity.defn(name="expire_run")
    async def expire_run(run_id: str) -> None:
        calls.expired.append(run_id)

    return [run_triage, resume_triage, expire_run]


@pytest.fixture
async def env() -> AsyncIterator[WorkflowEnvironment]:
    try:
        environment = await WorkflowEnvironment.start_time_skipping()
    except Exception as exc:
        pytest.skip(f"Temporal test server unavailable: {exc}")
    async with environment:
        yield environment


def _input(number: int = 1) -> TriageInput:
    return TriageInput(
        run_id=str(uuid.uuid4()),
        repo="o/r",
        number=number,
        title="t",
        body="b",
        url="u",
        created_at="2025-01-01T00:00:00+00:00",
    )


async def _wait_for_status(handle: Any, wanted: str) -> None:
    for _ in range(100):
        if await handle.query(TriageWorkflow.status) == wanted:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"workflow never reached {wanted}")


async def test_workflow_waits_for_the_approval_signal(env: WorkflowEnvironment) -> None:
    calls = Calls()
    async with Worker(
        env.client, task_queue=QUEUE, workflows=[TriageWorkflow], activities=activities(calls)
    ):
        inp = _input()
        handle = await env.client.start_workflow(
            TriageWorkflow.run, inp, id=workflow_id("o/r", 1), task_queue=QUEUE
        )
        await _wait_for_status(handle, "awaiting_approval")
        await handle.signal(TriageWorkflow.decide, Decision("approve", "nicanor"))
        result = await handle.result()
    assert result.status == "applied"
    assert calls.started == [inp.run_id]
    assert calls.resumed[0].decision.reviewer == "nicanor"


async def test_workflow_expires_without_a_decision(env: WorkflowEnvironment) -> None:
    calls = Calls()
    async with Worker(
        env.client, task_queue=QUEUE, workflows=[TriageWorkflow], activities=activities(calls)
    ):
        inp = _input(2)
        result = await env.client.execute_workflow(
            TriageWorkflow.run, inp, id=workflow_id("o/r", 2), task_queue=QUEUE
        )
    assert result.status == "expired"
    assert calls.expired == [inp.run_id] and calls.resumed == []


async def test_invalid_and_repeated_signals_are_ignored(env: WorkflowEnvironment) -> None:
    calls = Calls()
    async with Worker(
        env.client, task_queue=QUEUE, workflows=[TriageWorkflow], activities=activities(calls)
    ):
        handle = await env.client.start_workflow(
            TriageWorkflow.run, _input(3), id=workflow_id("o/r", 3), task_queue=QUEUE
        )
        await _wait_for_status(handle, "awaiting_approval")
        await handle.signal(TriageWorkflow.decide, Decision("delete-repo", "x"))
        await handle.signal(TriageWorkflow.decide, Decision("reject", "first"))
        await handle.signal(TriageWorkflow.decide, Decision("approve", "second"))
        result = await handle.result()
    assert result.status == "rejected"
    assert [r.decision.reviewer for r in calls.resumed] == ["first"]


async def test_start_triage_is_idempotent_per_issue(env: WorkflowEnvironment) -> None:
    calls = Calls()
    issue = IssueRecord(
        number=9,
        kind="issue",
        title="t",
        body="b",
        state="open",
        state_reason=None,
        author=None,
        labels=[],
        html_url="u",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        updated_at=datetime(2025, 1, 1, tzinfo=UTC),
        closed_at=None,
        comments_count=0,
    )
    client: Client = env.client
    async with Worker(
        client, task_queue=QUEUE, workflows=[TriageWorkflow], activities=activities(calls)
    ):
        first = await start_triage(client, QUEUE, "o/r", issue)
        second = await start_triage(client, QUEUE, "o/r", issue)
        await _wait_for_status(
            client.get_workflow_handle(workflow_id("o/r", 9)), "awaiting_approval"
        )
    assert first == second == run_id_for("o/r", 9)
    assert calls.started == [first]


# ------------------------------------------------------------------------------ trigger


def _event(**overrides: Any) -> IssueEvent:
    base: dict[str, Any] = {
        "type": "issue.opened",
        "repo": "o/r",
        "number": 1,
        "kind": "issue",
        "title": "t",
        "created_at": datetime(2025, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2025, 1, 1, tzinfo=UTC),
        "backfill": False,
    }
    return IssueEvent.model_validate({**base, **overrides})


def test_only_new_live_issues_trigger_triage() -> None:
    assert should_triage(_event())
    assert not should_triage(_event(backfill=True))
    assert not should_triage(_event(type="issue.updated"))
    assert not should_triage(_event(kind="pull_request"))


async def test_triage_handler_starts_once_per_issue_in_a_batch() -> None:
    started: list[int] = []

    class Reader:
        async def issue(self, repo: str, number: int) -> tuple[Any, list[Any]]:
            return type("I", (), {"number": number})(), []

    async def start(repo: str, issue: Any) -> str:
        started.append(issue.number)
        return "run"

    handler = make_triage_handler(Reader(), start)  # type: ignore[arg-type]
    await handler([_event(number=1), _event(number=1), _event(number=2, backfill=True)])
    assert started == [1]


# ------------------------------------------------------------------------------ api


def test_decision_endpoint_signals_the_workflow_and_validates_state() -> None:
    signals: list[tuple[str, Any]] = []

    class FakeHandle:
        def __init__(self, wid: str) -> None:
            self.wid = wid

        async def signal(self, _: Any, decision: Decision) -> None:
            signals.append((self.wid, decision))

    class FakeTemporal:
        def get_workflow_handle(self, wid: str) -> FakeHandle:
            return FakeHandle(wid)

    store = MemoryRunStore()
    for run_id, status in (("a", "awaiting_approval"), ("b", "applied")):
        store.runs[run_id] = RunRecord(
            run_id, "o/r", 5, "fake", "v1", "hybrid_rerank", status, title="t"
        )
    app = create_app()
    app.state.runs, app.state.temporal = store, FakeTemporal()
    client = TestClient(app)

    assert (
        client.post(
            "/api/runs/a/decision", json={"decision": "approve", "reviewer": "n"}
        ).status_code
        == 202
    )
    assert signals[0][0] == workflow_id("o/r", 5) and signals[0][1].decision == "approve"
    assert (
        client.post(
            "/api/runs/b/decision", json={"decision": "approve", "reviewer": "n"}
        ).status_code
        == 409
    )
    assert (
        client.post("/api/runs/a/decision", json={"decision": "edit", "reviewer": "n"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/runs/zz/decision", json={"decision": "approve", "reviewer": "n"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/runs/a/decision", json={"decision": "close-all", "reviewer": "n"}
        ).status_code
        == 422
    )
    listed = client.get("/api/runs", params={"status": "awaiting_approval"}).json()
    assert [r["run_id"] for r in listed] == ["a"]
