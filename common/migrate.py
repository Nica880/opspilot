"""Tiny forward-only SQL migration runner (see ADR-003)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import psycopg

from common.config import get_settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def pending(applied: set[str]) -> list[Path]:
    return [p for p in sorted(MIGRATIONS_DIR.glob("*.sql")) if p.stem not in applied]


async def migrate(dsn: str) -> list[str]:
    done: list[str] = []
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        rows = await (await conn.execute("SELECT version FROM schema_migrations")).fetchall()
        for path in pending({r[0] for r in rows}):
            async with conn.transaction():
                await conn.execute(path.read_text(encoding="utf-8").encode())
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,)
                )
            done.append(path.stem)
    return done


def main() -> None:
    applied = asyncio.run(migrate(get_settings().database_url))
    print(f"applied: {applied or 'nothing'}")


if __name__ == "__main__":
    main()
