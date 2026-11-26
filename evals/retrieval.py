"""Duplicate-retrieval eval: recall@k and MRR for dense vs hybrid vs hybrid + rerank.

Time-split by construction: each query only sees issues created strictly before it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from evals.ground_truth import DuplicateCase, duplicate_cases
from indexing.chunking import doc_key_issue
from indexing.qdrant_index import SearchFilters
from indexing.search import VARIANTS, SearchService, Variant

KS = (1, 5, 10)
ISSUE_KINDS = ("issue", "pull_request", "discussion")


@dataclass
class RetrievalMetrics:
    variant: str
    n: int
    recall: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[idx]


def score_ranks(ranks: list[int | None], ks: Sequence[int] = KS) -> tuple[dict[int, float], float]:
    """`ranks` are 1-based positions of the target, None when not retrieved."""
    n = len(ranks) or 1
    recall = {k: sum(1 for r in ranks if r is not None and r <= k) / n for k in ks}
    mrr = sum(1.0 / r for r in ranks if r is not None) / n
    return recall, mrr


async def evaluate(
    search: SearchService, cases: list[DuplicateCase], variant: Variant, k: int = max(KS)
) -> RetrievalMetrics:
    ranks: list[int | None] = []
    latencies: list[float] = []
    for case in cases:
        filters = SearchFilters(
            repos=[case.repo],
            kinds=ISSUE_KINDS,
            created_before=case.created_at,
            exclude_doc_keys=[doc_key_issue(case.repo, case.number)],
        )
        started = time.perf_counter()
        hits = await search.search(
            f"{case.title}\n\n{case.body}", variant=variant, k=k, filters=filters
        )
        latencies.append((time.perf_counter() - started) * 1000)
        target = doc_key_issue(case.repo, case.target)
        keys = [h.doc_key for h in hits]
        ranks.append(keys.index(target) + 1 if target in keys else None)
    recall, mrr = score_ranks(ranks)
    return RetrievalMetrics(
        variant=variant,
        n=len(cases),
        recall=recall,
        mrr=mrr,
        p50_ms=percentile(latencies, 0.5),
        p95_ms=percentile(latencies, 0.95),
    )


def to_markdown(results: list[RetrievalMetrics], meta: dict[str, str]) -> str:
    lines = [
        "## Duplicate retrieval",
        "",
        " · ".join(f"{k}: {v}" for k, v in meta.items()),
        "",
        "| Variant | n | recall@1 | recall@5 | recall@10 | MRR | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.variant} | {r.n} | {r.recall[1]:.3f} | {r.recall[5]:.3f} | {r.recall[10]:.3f} "
            f"| {r.mrr:.3f} | {r.p50_ms:.0f} | {r.p95_ms:.0f} |"
        )
    return "\n".join(lines) + "\n"


async def main_async(
    variants: Sequence[Variant],
) -> tuple[list[RetrievalMetrics], dict[str, str]]:
    from common.config import get_settings
    from common.db import close_pool, get_pool
    from indexing.corpus import CorpusReader
    from indexing.factory import build_search

    settings = get_settings()
    search = await build_search(settings)
    reader = CorpusReader(await get_pool())
    cases: list[DuplicateCase] = []
    try:
        for repo in settings.repos:
            threads = [(i, c) async for _, i, c in reader.iter_issues(repo)]
            cases.extend(duplicate_cases(repo, threads))
    finally:
        await close_pool()
    results = [await evaluate(search, cases, v) for v in variants]
    meta = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "cases": str(len(cases)),
        "embedder": settings.embed_model if settings.llm_provider != "fake" else "hashing (fake)",
        "mode": settings.mode,
    }
    return results, meta


def write_report(results: list[RetrievalMetrics], meta: dict[str, str], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{meta['date']}-retrieval.md"
    path.write_text(to_markdown(results, meta), encoding="utf-8")
    path.with_suffix(".json").write_text(
        json.dumps({"meta": meta, "results": [asdict(r) for r in results]}, indent=2),
        encoding="utf-8",
    )
    return path


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="evals.retrieval")
    parser.add_argument("--variants", nargs="*", choices=VARIANTS, default=list(VARIANTS))
    parser.add_argument("--out", type=Path, default=Path("docs/eval"))
    args = parser.parse_args(argv)
    results, meta = asyncio.run(main_async(args.variants))
    print(f"wrote {write_report(results, meta, args.out)}")
    for r in results:
        print(r)


if __name__ == "__main__":
    main()
