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
    """Temporal's auto-setup container takes a while on first boot; retry instead of crashing."""
    for attempt in range(1, attempts + 1):
        try:
            return await Client.connect(address, namespace=namespace)
        except RuntimeError as exc:
            log.warning("temporal not ready (%s/%s): %s", attempt, attempts, exc)
            await asyncio.sleep(5)
    raise RuntimeError(f"could not connect to Temporal at {address}")


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
