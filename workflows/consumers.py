"""Redpanda consumers. `python -m workflows.consumers indexer` keeps Qdrant in sync."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from common.config import get_settings
from common.db import get_pool
from indexing.corpus import CorpusReader
from indexing.factory import build_search
from indexing.indexer import Indexer
from ingestion.events import DOCS_TOPIC, ISSUES_TOPIC
from ingestion.models import DocEvent, IssueEvent

log = logging.getLogger(__name__)

Event = IssueEvent | DocEvent
Handler = Callable[[list[Event]], Awaitable[None]]


def parse_event(topic: str, value: bytes) -> Event:
    data: dict[str, Any] = json.loads(value)
    if topic == ISSUES_TOPIC:
        return IssueEvent.model_validate(data)
    return DocEvent.model_validate(data)


def dedupe(events: list[Event]) -> list[Event]:
    """Keep the last event per key: re-indexing an issue twice in one batch is wasted work."""
    latest: dict[str, Event] = {}
    for event in events:
        latest[event.key] = event
    return list(latest.values())


def make_index_handler(reader: CorpusReader, indexer: Indexer) -> Handler:
    async def handle(events: list[Event]) -> None:
        for event in dedupe(events):
            if isinstance(event, IssueEvent):
                found = await reader.issue(event.repo, event.number)
                if found is not None:
                    await indexer.index_issue(event.repo, found[0], found[1])
            else:
                doc = await reader.doc(event.repo, event.path)
                if doc is not None:
                    await indexer.index_doc(event.repo, doc)

    return handle


async def consume(
    topics: Sequence[str], group_id: str, handler: Handler, bootstrap: str, batch: int = 50
) -> None:
    """At-least-once: offsets are committed only after the handler finished the batch."""
    from aiokafka import AIOKafkaConsumer

    consumer = AIOKafkaConsumer(
        *topics,
        bootstrap_servers=bootstrap,
        group_id=group_id,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        while True:
            records = await consumer.getmany(timeout_ms=2000, max_records=batch)
            events = [
                parse_event(tp.topic, msg.value)
                for tp, messages in records.items()
                for msg in messages
            ]
            if events:
                await handler(events)
                await consumer.commit()
                log.info("%s handled %d events", group_id, len(events))
    finally:
        await consumer.stop()


async def run_indexer() -> None:
    settings = get_settings()
    search = await build_search(settings)
    indexer = Indexer(search.index, search.embedder, search.sparse)
    reader = CorpusReader(await get_pool())
    await consume(
        [ISSUES_TOPIC, DOCS_TOPIC],
        "opspilot-indexer",
        make_index_handler(reader, indexer),
        settings.kafka_bootstrap,
    )


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="workflows.consumers")
    parser.add_argument("consumer", choices=["indexer"])
    parser.parse_args(argv)
    asyncio.run(run_indexer())


if __name__ == "__main__":
    main()
