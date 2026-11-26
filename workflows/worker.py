"""`python -m workflows.worker`: Temporal worker for triage workflows and activities."""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from common.config import get_settings
from common.tracing import setup_tracing
from workflows.activities import ACTIVITIES
from workflows.triage import TriageWorkflow

log = logging.getLogger(__name__)


async def connect(address: str, namespace: str, attempts: int = 30) -> Client:
    """Temporal's auto-setup image serves the frontend before it registers the namespace, and
    `Worker.run()` fails fast on a missing namespace: wait for both instead of crashing."""
    from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest

    for attempt in range(1, attempts + 1):
        try:
            client = await Client.connect(address, namespace=namespace)
            await client.workflow_service.describe_namespace(
                DescribeNamespaceRequest(namespace=namespace)
            )
            return client
        except Exception as exc:  # RPCError or connection error while Temporal boots
            log.warning("temporal not ready (%s/%s): %s", attempt, attempts, exc)
            await asyncio.sleep(5)
    raise RuntimeError(f"Temporal at {address} has no namespace {namespace!r}")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    setup_tracing("opspilot-worker")
    client = await connect(settings.temporal_address, settings.temporal_namespace)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[TriageWorkflow],
        activities=ACTIVITIES,
        max_concurrent_activities=2,  # one 7B model on a laptop: keep LLM calls bounded
    )
    log.info("worker polling %s", settings.temporal_task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
