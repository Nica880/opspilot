"""`python -m workflows.demo --n 5`: start triage on the newest ingested open issues."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from common.config import get_settings
from common.db import close_pool, get_pool
from indexing.corpus import CorpusReader
from workflows.client import start_triage
from workflows.worker import connect


async def newest_open_issues(n: int) -> list[tuple[str, int]]:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT r.full_name, i.number FROM issues i JOIN repos r ON r.id = i.repo_id
            WHERE i.kind = 'issue' AND i.state = 'open'
            ORDER BY i.created_at DESC LIMIT %s
            """,
            (n,),
        )
        rows: list[Any] = await cur.fetchall()
    return [(r["full_name"], r["number"]) for r in rows]


async def main_async(n: int) -> None:
    settings = get_settings()
    client = await connect(settings.temporal_address, settings.temporal_namespace)
    reader = CorpusReader(await get_pool())
    try:
        for repo, number in await newest_open_issues(n):
            found = await reader.issue(repo, number)
            if found is None:
                continue
            run_id = await start_triage(client, settings.temporal_task_queue, repo, found[0])
            print(f"{repo}#{number}: run {run_id}")
    finally:
        await close_pool()
    print("Approval queue: http://localhost:3000/approvals · Temporal UI: http://localhost:8233")


def main() -> None:
    parser = argparse.ArgumentParser(prog="workflows.demo")
    parser.add_argument("--n", type=int, default=5)
    asyncio.run(main_async(parser.parse_args().n))


if __name__ == "__main__":
    main()
