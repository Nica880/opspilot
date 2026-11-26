"""LLM providers and the structured-output loop (schema-constrained, validated, max 2 repairs)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)
MAX_REPAIRS = 2


class Message(dict[str, str]):
    @classmethod
    def of(cls, role: str, content: str) -> Message:
        return cls(role=role, content=content)


class LLMProvider(Protocol):
    name: str

    async def complete(
        self, messages: list[Message], schema: dict[str, Any], *, task: str
    ) -> str: ...


class StructuredOutputError(RuntimeError):
    """Raised after the repair budget is spent; the run goes to the human queue."""

    def __init__(self, task: str, errors: list[str]) -> None:
        super().__init__(f"{task}: invalid structured output after {len(errors)} attempts")
        self.task = task
        self.errors = errors


@dataclass
class CallRecord:
    task: str
    attempts: int
    ok: bool


@dataclass
class StructuredLLM:
    provider: LLMProvider
    calls: list[CallRecord] = field(default_factory=list)

    async def generate(self, task: str, messages: list[Message], schema_type: type[T]) -> T:
        schema = schema_type.model_json_schema()
        convo = list(messages)
        errors: list[str] = []
        for attempt in range(1 + MAX_REPAIRS):
            raw = await self.provider.complete(convo, schema, task=task)
            try:
                result = schema_type.model_validate_json(raw)
            except ValidationError as exc:
                errors.append(str(exc)[:500])
                convo = [
                    *convo,
                    Message.of("assistant", raw[:4000]),
                    Message.of(
                        "user",
                        "That JSON did not match the schema. Errors:\n"
                        f"{exc.errors(include_url=False)}\n"
                        "Reply again with ONLY corrected JSON. Every key is required.",
                    ),
                ]
                continue
            self.calls.append(CallRecord(task, attempt + 1, True))
            return result
        self.calls.append(CallRecord(task, 1 + MAX_REPAIRS, False))
        raise StructuredOutputError(task, errors)


class OllamaProvider:
    """`format` = JSON schema makes Ollama constrain decoding to the schema (Ollama >= 0.5)."""

    def __init__(self, base_url: str, model: str, *, num_ctx: int = 8192) -> None:
        self.name = model
        self._http = httpx.AsyncClient(base_url=base_url, timeout=300.0)
        self._num_ctx = num_ctx

    async def complete(self, messages: list[Message], schema: dict[str, Any], *, task: str) -> str:
        response = await self._http.post(
            "/api/chat",
            json={
                "model": self.name,
                "messages": list(messages),
                "format": schema,
                "stream": False,
                "options": {"temperature": 0, "num_ctx": self._num_ctx, "seed": 7},
            },
        )
        response.raise_for_status()
        return str(response.json()["message"]["content"])

    async def aclose(self) -> None:
        await self._http.aclose()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
