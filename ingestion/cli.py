"""`python -m ingestion.cli {backfill,incremental,docs}`: runs against the compose stack."""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Sequence

from common.config import Settings, get_settings
from common.db import close_pool, get_pool
from ingestion.events import KafkaPublisher
from ingestion.github import GitHubClient
from ingestion.pipeline import (
    SyncStats,
    backfill_repo,
    incremental_repo,
    ingest_discussions,
    ingest_docs,
)
from ingestion.store import PgHttpCache, PgStore

SMALL_DOCS_PER_REPO = 150
SMALL_DISCUSSIONS_PER_REPO = 100
FULL_DISCUSSIONS_PER_REPO = 2000


async def run(command: str, repos: Sequence[str], settings: Settings) -> list[SyncStats]:
    token = settings.github_token.get_secret_value() if settings.github_token else None
    if token is None:
        logging.warning("GITHUB_TOKEN is not set: unauthenticated limit is 60 requests/hour")
    small = settings.mode == "small"
    pool = await get_pool()
    store = PgStore(pool)
    results: list[SyncStats] = []
    try:
        async with (
            GitHubClient(token, cache=PgHttpCache(pool)) as gh,
            KafkaPublisher(settings.kafka_bootstrap) as publisher,
        ):
            for repo in repos:
                if command == "backfill":
                    limit = settings.small_mode_issues_per_repo if small else None
                    results.append(await backfill_repo(gh, store, publisher, repo, limit=limit))
                    if token is not None:
                        per_repo = (
                            SMALL_DISCUSSIONS_PER_REPO if small else FULL_DISCUSSIONS_PER_REPO
                        )
                        results.append(
                            await ingest_discussions(gh, store, publisher, repo, limit=per_repo)
                        )
                elif command == "incremental":
                    results.append(await incremental_repo(gh, store, publisher, repo))
                elif command == "docs":
                    limit = SMALL_DOCS_PER_REPO if small else None
                    results.append(await ingest_docs(gh, store, publisher, repo, limit=limit))
            logging.info("github requests: %s", gh.stats)
    finally:
        await close_pool()
    return results


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="ingestion.cli")
    parser.add_argument("command", choices=["backfill", "incremental", "docs"])
    parser.add_argument("--repos", nargs="*", help="owner/name; defaults to REPOS")
    args = parser.parse_args(argv)
    settings = get_settings()
    for stats in asyncio.run(run(args.command, args.repos or settings.repos, settings)):
        print(stats)


if __name__ == "__main__":
    main()
