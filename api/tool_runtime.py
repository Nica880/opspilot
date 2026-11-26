"""Production wiring for ToolService (lazy: nothing connects until a tool is called)."""

from __future__ import annotations

import asyncio

from agent.llm import StructuredLLM
from agent.runs import PgRunStore, RunStore
from agent.tools import ToolService
from common.config import Settings
from common.db import get_pool
from indexing.corpus import CorpusReader
from indexing.search import SearchService
from ingestion.models import IssueRecord


def build_tool_service(settings: Settings) -> ToolService:
    from agent.runtime import build_provider

    lock = asyncio.Lock()
    cache: dict[str, object] = {}

    async def search() -> SearchService:
        async with lock:
            if "search" not in cache:
                from indexing.factory import build_search

                cache["search"] = await build_search(settings)
        service = cache["search"]
        assert isinstance(service, SearchService)
        return service

    async def reader() -> CorpusReader:
        return CorpusReader(await get_pool())

    async def runs() -> RunStore:
        return PgRunStore(await get_pool())

    async def start(repo: str, issue: IssueRecord) -> str:
        from workflows.client import start_triage
        from workflows.worker import connect

        if "temporal" not in cache:
            cache["temporal"] = await connect(
                settings.temporal_address, settings.temporal_namespace, 3
            )
        return await start_triage(cache["temporal"], settings.temporal_task_queue, repo, issue)  # type: ignore[arg-type]

    return ToolService(
        repos=settings.repos,
        search=search,
        reader=reader,
        runs=runs,
        llm=lambda: StructuredLLM(build_provider(settings)),
        start_triage=start,
        prompt_version=settings.prompt_version,
    )
