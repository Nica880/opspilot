"""Start triage workflows and send decisions. Idempotent: one workflow (and run id) per issue."""

from __future__ import annotations

import contextlib
import uuid

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from ingestion.models import IssueRecord
from workflows.triage import Decision, TriageInput, TriageWorkflow, workflow_id

RUN_NAMESPACE = uuid.UUID("0b5e2a1e-7f2c-4a51-9d6e-6f7073706c74")


def run_id_for(repo: str, number: int) -> str:
    return str(uuid.uuid5(RUN_NAMESPACE, workflow_id(repo, number)))


async def start_triage(client: Client, task_queue: str, repo: str, issue: IssueRecord) -> str:
    run_id = run_id_for(repo, issue.number)
    with contextlib.suppress(WorkflowAlreadyStartedError):
        await client.start_workflow(
            TriageWorkflow.run,
            TriageInput(
                run_id=run_id,
                repo=repo,
                number=issue.number,
                title=issue.title,
                body=issue.body,
                url=issue.html_url,
                created_at=issue.created_at.isoformat(),
            ),
            id=workflow_id(repo, issue.number),
            task_queue=task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
    return run_id


async def send_decision(client: Client, repo: str, number: int, decision: Decision) -> None:
    handle = client.get_workflow_handle(workflow_id(repo, number))
    await handle.signal(TriageWorkflow.decide, decision)
