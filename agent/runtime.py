"""Wire the real triage service from settings (used by the Temporal worker and the API)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from agent.actions import SandboxActions
from agent.fake_llm import FakeProvider
from agent.graph import TriageDeps, build_graph
from agent.llm import LLMProvider, OllamaProvider, StructuredLLM
from agent.runs import PgRunStore
from agent.service import TriageConfig, TriageService
from agent.taxonomy import top_labels
from common.config import Settings
from common.db import get_pool
from indexing.factory import build_search

log = logging.getLogger(__name__)


def build_provider(settings: Settings, model: str | None = None) -> LLMProvider:
    if settings.llm_provider == "fake":
        return FakeProvider()
    return OllamaProvider(settings.ollama_url, model or settings.llm_model)


def build_actions(settings: Settings) -> SandboxActions | None:
    import os

    token = os.environ.get("SANDBOX_GITHUB_TOKEN")
    if not settings.sandbox_repo or not token:
        log.warning("SANDBOX_REPO or SANDBOX_GITHUB_TOKEN missing: approvals are recorded only")
        return None
    return SandboxActions(token, settings.sandbox_repo)


async def observed_labels(repos: tuple[str, ...]) -> dict[str, list[str]]:
    pool = await get_pool()
    out: dict[str, list[str]] = {}
    async with pool.connection() as conn:
        for repo in repos:
            cur = await conn.execute(
                """SELECT i.labels FROM issues i JOIN repos r ON r.id = i.repo_id
                   WHERE r.full_name = %s AND i.kind = 'issue'""",
                (repo,),
            )
            rows: list[Any] = await cur.fetchall()
            out[repo] = top_labels([row["labels"] for row in rows])
    return out


@dataclass
class Runtime:
    service: TriageService
    settings: Settings


async def build_runtime(settings: Settings) -> Runtime:
    pool = await get_pool()
    saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
    await saver.setup()
    search = await build_search(settings)
    deps = TriageDeps(
        llm=StructuredLLM(build_provider(settings)),
        search=search,
        actions=build_actions(settings),
    )
    service = TriageService(
        build_graph(deps, saver),
        PgRunStore(pool),
        TriageConfig(
            model=settings.llm_model if settings.llm_provider != "fake" else "fake",
            prompt_version=settings.prompt_version,
        ),
        labels_by_repo=await observed_labels(settings.repos),
    )
    return Runtime(service, settings)
