"""`python -m indexing.cli {reindex,search}`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections.abc import Sequence

from common.config import get_settings
from common.db import close_pool, get_pool
from indexing.corpus import CorpusReader
from indexing.factory import build_search
from indexing.indexer import Indexer
from indexing.qdrant_index import SearchFilters
from indexing.search import VARIANTS, Variant


async def reindex(repo: str | None) -> None:
    settings = get_settings()
    search = await build_search(settings)
    indexer = Indexer(search.index, search.embedder, search.sparse)
    reader = CorpusReader(await get_pool())
    started = time.perf_counter()
    try:
        async for full_name, issue, comments in reader.iter_issues(repo):
            await indexer.index_issue(full_name, issue, comments)
            if indexer.stats.documents % 200 == 0:
                logging.info("indexed %s documents", indexer.stats.documents)
        for full_name, path in await reader.all_doc_keys():
            if repo is not None and full_name != repo:
                continue
            doc = await reader.doc(full_name, path)
            if doc is not None:
                await indexer.index_doc(full_name, doc)
    finally:
        await close_pool()
    elapsed = time.perf_counter() - started
    print(
        f"indexed {indexer.stats.documents} documents / {indexer.stats.chunks} chunks "
        f"in {elapsed:.0f}s; collection has {await search.index.count()} points"
    )


async def search_cmd(query: str, variant: Variant, k: int, repo: str | None) -> None:
    search = await build_search(get_settings())
    filters = SearchFilters(repos=[repo] if repo else None)
    for hit in await search.search(query, variant=variant, k=k, filters=filters):
        print(f"{hit.score:8.4f}  {hit.kind:13} {hit.doc_key:40} {hit.title[:60]}")


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="indexing.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    re_p = sub.add_parser("reindex")
    re_p.add_argument("--repo")
    s_p = sub.add_parser("search")
    s_p.add_argument("query")
    s_p.add_argument("--variant", choices=VARIANTS, default="hybrid_rerank")
    s_p.add_argument("-k", type=int, default=10)
    s_p.add_argument("--repo")
    args = parser.parse_args(argv)
    if args.command == "reindex":
        asyncio.run(reindex(args.repo))
    else:
        asyncio.run(search_cmd(args.query, args.variant, args.k, args.repo))


if __name__ == "__main__":
    main()
