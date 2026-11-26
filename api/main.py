"""FastAPI entrypoint: REST + SSE for the UI, MCP (streamable HTTP) at /mcp/, A2A at /a2a/v1."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.tools import ToolService
from api.a2a import router as a2a_router
from api.mcp_server import LOCAL_SECURITY, build_mcp, default_tools
from api.routes_evals import router as evals_router
from api.routes_runs import router as runs_router
from api.routes_search import router as search_router
from api.routes_ui import router as ui_router
from common.config import get_settings


def create_app(tools: ToolService | None = None) -> FastAPI:
    holder: dict[str, ToolService | None] = {"tools": tools}

    def current_tools() -> ToolService:
        return holder["tools"] or default_tools()

    mcp = build_mcp(current_tools)
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=LOCAL_SECURITY,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="OpsPilot", version="0.1.0", lifespan=lifespan)
    app.state.tools = tools
    app.state.mcp = mcp
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        settings = get_settings()
        return {"status": "ok", "mode": settings.mode, "llm_provider": settings.llm_provider}

    app.include_router(search_router)
    app.include_router(runs_router)
    app.include_router(evals_router)
    app.include_router(ui_router)
    app.include_router(a2a_router)
    app.mount("/mcp", mcp_app)
    return app


app = create_app()
