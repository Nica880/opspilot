"""Search endpoint: the playground in the UI compares variants side by side."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from indexing.qdrant_index import SearchFilters
from indexing.search import SearchService, Variant

router = APIRouter(prefix="/api", tags=["search"])


class SearchHitOut(BaseModel):
    doc_key: str
    score: float
    kind: str
    repo: str
    title: str
    url: str
    snippet: str
    number: int | None = None


class SearchResponse(BaseModel):
    query: str
    variant: Variant
    hits: list[SearchHitOut]


async def get_search(request: Request) -> SearchService:
    service: SearchService | None = getattr(request.app.state, "search", None)
    if service is None:
        from common.config import get_settings
        from indexing.factory import build_search

        service = await build_search(get_settings())
        request.app.state.search = service
    return service


@router.get("/search", response_model=SearchResponse)
async def search(
    search_service: Annotated[SearchService, Depends(get_search)],
    q: Annotated[str, Query(min_length=2, max_length=2000)],
    variant: Variant = "hybrid_rerank",
    k: Annotated[int, Query(ge=1, le=50)] = 10,
    repo: str | None = None,
    kind: Annotated[list[str] | None, Query()] = None,
) -> SearchResponse:
    filters = SearchFilters(repos=[repo] if repo else None, kinds=kind)
    hits = await search_service.search(q, variant=variant, k=k, filters=filters)
    return SearchResponse(
        query=q,
        variant=variant,
        hits=[
            SearchHitOut(
                doc_key=h.doc_key,
                score=h.score,
                kind=h.kind,
                repo=h.repo,
                title=h.title,
                url=h.url,
                snippet=h.text[:400],
                number=h.number,
            )
            for h in hits
        ],
    )
