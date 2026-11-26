"""Event publishing to Redpanda (Kafka API). Topics are versioned: `issues.v1`, `docs.v1`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel

from ingestion.models import DocEvent, IssueEvent

ISSUES_TOPIC = "issues.v1"
DOCS_TOPIC = "docs.v1"


def topic_for(event: IssueEvent | DocEvent) -> str:
    return ISSUES_TOPIC if isinstance(event, IssueEvent) else DOCS_TOPIC


class Publisher(Protocol):
    async def publish(self, event: IssueEvent | DocEvent) -> None: ...

    async def flush(self) -> None: ...


@dataclass
class MemoryPublisher:
    sent: list[tuple[str, str, BaseModel]] = field(default_factory=list)

    async def publish(self, event: IssueEvent | DocEvent) -> None:
        self.sent.append((topic_for(event), event.key, event))

    async def flush(self) -> None:
        return None


class KafkaPublisher:
    """Idempotent producer; key = `repo#number` so all events of one issue stay ordered."""

    def __init__(self, bootstrap: str) -> None:
        from aiokafka import AIOKafkaProducer

        self._producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap, enable_idempotence=True, acks="all", linger_ms=20
        )

    async def __aenter__(self) -> KafkaPublisher:
        await self._producer.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._producer.stop()

    async def publish(self, event: IssueEvent | DocEvent) -> None:
        await self._producer.send(
            topic_for(event),
            key=event.key.encode(),
            value=event.model_dump_json().encode(),
        )

    async def flush(self) -> None:
        await self._producer.flush()
