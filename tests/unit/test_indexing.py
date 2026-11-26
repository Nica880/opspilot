from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient

from api.main import create_app
from evals.ground_truth import duplicate_cases, find_duplicate_target, linked_answers
from evals.retrieval import evaluate, score_ranks
from indexing.chunking import clean, doc_chunks, issue_chunks, split_text
from indexing.embeddings import HashingEmbedder, SimpleBm25
from indexing.indexer import Indexer
from indexing.qdrant_index import QdrantIndex, SearchFilters
from indexing.reranker import OverlapReranker
from indexing.search import SearchService
from ingestion.models import CommentRecord, DocRecord, IssueRecord

T0 = datetime(2025, 1, 1, tzinfo=UTC)
REPO = "fastapi/fastapi"


def issue(
    number: int, title: str, body: str = "", days: int = 0, kind: str = "issue"
) -> IssueRecord:
    created = T0 + timedelta(days=days)
    return IssueRecord(
        number=number,
        kind=kind,
        title=title,
        body=body,
        state="open",
        state_reason=None,
        author="user",
        labels=[],
        html_url=f"https://github.com/{REPO}/issues/{number}",
        created_at=created,
        updated_at=created,
        closed_at=None,
        comments_count=0,
    )


def comment(cid: int, number: int, body: str, role: str = "NONE", days: int = 0) -> CommentRecord:
    created = T0 + timedelta(days=days)
    return CommentRecord(
        id=cid,
        issue_number=number,
        author="someone",
        author_association=role,
        body=body,
        html_url=f"https://github.com/{REPO}/issues/{number}#issuecomment-{cid}",
        created_at=created,
        updated_at=created,
    )


CORPUS = [
    (
        issue(
            1,
            "Dependency with yield raises RuntimeError on shutdown",
            "Using yield in Depends crashes with RuntimeError when the app stops.",
            0,
        ),
        [],
    ),
    (
        issue(
            2, "OpenAPI schema missing examples for Body", "Body(examples=...) not shown in docs", 1
        ),
        [],
    ),
    (
        issue(
            3,
            "Websocket disconnect not detected",
            "WebSocketDisconnect never raised behind nginx",
            2,
        ),
        [],
    ),
    (
        issue(4, "Upload large files streaming", "UploadFile reads the whole file into memory", 3),
        [],
    ),
    (
        issue(
            5,
            "RuntimeError at shutdown when dependency uses yield",
            "Same crash: dependency with yield, RuntimeError on app shutdown.",
            10,
        ),
        [comment(50, 5, "Duplicate of #1", "MEMBER", 11)],
    ),
]


async def build_service() -> SearchService:
    embedder, sparse = HashingEmbedder(), SimpleBm25()
    index = QdrantIndex(AsyncQdrantClient(location=":memory:"), embedder.dim)
    await index.ensure_collection()
    indexer = Indexer(index, embedder, sparse)
    for record, comments in CORPUS:
        await indexer.index_issue(REPO, record, comments)
    await indexer.index_doc(
        REPO,
        DocRecord(
            path="docs/en/docs/tutorial/dependencies/dependencies-with-yield.md",
            url="https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/",
            title="Dependencies with yield",
            content="# Dependencies with yield\n\nIntro.\n\n## Exceptions and yield\n\nRaise after yield to handle RuntimeError.",
            sha="x",
        ),
    )
    return SearchService(index, embedder, sparse, OverlapReranker())


# ------------------------------------------------------------------------------ chunking


def test_clean_strips_template_comments() -> None:
    assert clean("<!-- template\nfill me -->\nReal text\n\n\n\nmore") == "Real text\n\nmore"


def test_split_text_bounds_and_overlap() -> None:
    text = "a" * 3500
    parts = split_text(text, max_chars=1500, overlap=200)
    assert all(len(p) <= 1500 for p in parts)
    assert len(parts) == 3
    assert parts[0][-200:] == parts[1][:200]
    assert split_text("short") == ["short"]
    assert split_text("   ") == []


def test_issue_chunks_put_maintainer_answers_first_and_ids_are_stable() -> None:
    rec = issue(9, "Title", "Body")
    comments = [
        comment(1, 9, "user says hi", "NONE", 1),
        comment(2, 9, "maintainer answer", "MEMBER", 2),
    ]
    chunks = issue_chunks(REPO, rec, comments)
    assert [c.payload["section"] for c in chunks] == ["body", "comment", "comment"]
    assert chunks[1].payload["maintainer"] is True and "maintainer answer" in chunks[1].text
    assert [c.id for c in chunks] == [c.id for c in issue_chunks(REPO, rec, comments)]


def test_doc_chunks_link_to_section_anchors() -> None:
    doc = DocRecord(
        path="docs/x.md",
        url="https://d/x/",
        title="X",
        content="# X\n\nintro\n\n## Error Handling!\n\nbody",
        sha="s",
    )
    urls = [c.payload["url"] for c in doc_chunks(REPO, doc)]
    assert urls == ["https://d/x/", "https://d/x/#error-handling"]


# ------------------------------------------------------------------------------ search


@pytest.mark.parametrize("variant", ["dense", "sparse", "hybrid", "hybrid_rerank"])
async def test_every_variant_finds_the_original_for_a_duplicate(variant: str) -> None:
    service = await build_service()
    hits = await service.search(
        "RuntimeError at shutdown when dependency uses yield",
        variant=variant,  # type: ignore[arg-type]
        k=3,
        filters=SearchFilters(kinds=["issue"], exclude_doc_keys=[f"{REPO}#5"]),
    )
    assert hits[0].doc_key == f"{REPO}#1"


async def test_filters_prevent_time_leakage_and_exclude_docs() -> None:
    service = await build_service()
    hits = await service.search(
        "dependency yield RuntimeError",
        variant="hybrid",
        k=10,
        filters=SearchFilters(kinds=["issue"], created_before=T0 + timedelta(days=2)),
    )
    keys = {h.doc_key for h in hits}
    assert keys <= {f"{REPO}#1", f"{REPO}#2"}
    assert f"{REPO}#5" not in keys


async def test_docs_are_searchable_with_anchor_urls() -> None:
    service = await build_service()
    hits = await service.search(
        "raise after yield", variant="hybrid", k=3, filters=SearchFilters(kinds=["doc"])
    )
    assert hits[0].url.endswith("dependencies-with-yield/#exceptions-and-yield")


async def test_reindexing_replaces_stale_chunks() -> None:
    embedder, sparse = HashingEmbedder(), SimpleBm25()
    index = QdrantIndex(AsyncQdrantClient(location=":memory:"), embedder.dim)
    await index.ensure_collection()
    indexer = Indexer(index, embedder, sparse)
    rec = issue(7, "t", "b")
    await indexer.index_issue(REPO, rec, [comment(i, 7, f"c{i}") for i in range(3)])
    assert await index.count() == 4
    await indexer.index_issue(REPO, rec, [comment(0, 7, "c0")])
    assert await index.count() == 2


async def test_search_api_endpoint() -> None:
    app = create_app()
    app.state.search = await build_service()
    response = TestClient(app).get(
        "/api/search", params={"q": "websocket disconnect nginx", "variant": "hybrid", "k": 2}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["hits"][0]["doc_key"] == f"{REPO}#3"
    assert len(body["hits"]) == 2


# ------------------------------------------------------------------------------ eval


def test_score_ranks() -> None:
    recall, mrr = score_ranks([1, 3, None, 6], ks=(1, 5, 10))
    assert recall == {1: 0.25, 5: 0.5, 10: 0.75}
    assert mrr == pytest.approx((1 + 1 / 3 + 1 / 6) / 4)


def test_duplicate_ground_truth_requires_an_earlier_target() -> None:
    later_target = (issue(20, "a", days=1), [comment(1, 20, "Duplicate of #21", "MEMBER")])
    target: tuple[IssueRecord, list[CommentRecord]] = (issue(21, "b", days=5), [])
    assert duplicate_cases(REPO, [later_target, target]) == []
    assert find_duplicate_target(issue(3, "x"), [comment(1, 3, "Thanks!\nDuplicate of #2")]) == 2
    assert (
        find_duplicate_target(issue(3, "x"), [comment(1, 3, "not a duplicate of #2 really")])
        is None
    )
    cases = duplicate_cases(REPO, CORPUS)
    assert [(c.number, c.target) for c in cases] == [(5, 1)]


def test_linked_answers_only_from_maintainers() -> None:
    comments = [
        comment(1, 1, "see https://fastapi.tiangolo.com/tutorial/dependencies/#yield.", "MEMBER"),
        comment(2, 1, "random https://docs.pydantic.dev/latest/x/", "NONE"),
        comment(3, 1, "fixed in https://github.com/fastapi/fastapi/pull/123", "OWNER"),
    ]
    assert linked_answers(comments) == [
        "https://fastapi.tiangolo.com/tutorial/dependencies/",
        "https://github.com/fastapi/fastapi/pull/123",
    ]


async def test_retrieval_eval_end_to_end_on_synthetic_corpus() -> None:
    service = await build_service()
    metrics = await evaluate(service, duplicate_cases(REPO, CORPUS), "hybrid_rerank")
    assert metrics.n == 1
    assert metrics.recall[1] == 1.0 and metrics.mrr == 1.0
