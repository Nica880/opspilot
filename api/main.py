"""FastAPI entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes_evals import router as evals_router
from api.routes_runs import router as runs_router
from api.routes_search import router as search_router
from api.routes_ui import router as ui_router
from common.config import get_settings


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="OpsPilot", version="0.1.0", lifespan=lifespan)
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
    return app


app = create_app()
