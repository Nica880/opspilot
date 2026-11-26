"""Endpoints for the UI: inbox, issue thread, live run stream (SSE), ops."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from agent.runs import RunRecord, RunStore
from api.routes_runs import RunOut, get_runs

router = APIRouter(prefix="/api", tags=["ui"])
Runs = Annotated[RunStore, Depends(get_runs)]
STREAM_INTERVAL_S = 2.0


async def _pool(request: Request) -> Any:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        from common.db import get_pool

        pool = await get_pool()
    return pool


def run_summary(run: RunRecord) -> dict[str, Any]:
    s = run.suggestions or {}
    classification = s.get("classification") or {}
    return {
        "run_id": run.run_id,
        "repo": run.repo,
        "number": run.number,
        "title": run.title,
        "status": run.status,
        "steps": [step.get("node") for step in run.steps],
        "type": classification.get("type"),
        "severity": classification.get("severity"),
        "labels": s.get("labels") or [],
        "needs_human": bool(s.get("needs_human")),
        "latency_ms": run.latency_ms,
    }


@router.get("/inbox")
async def inbox(request: Request, runs: Runs, limit: int = 50) -> list[dict[str, Any]]:
    pool = await _pool(request)
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT r.full_name AS repo, i.number, i.title, i.html_url AS url, i.created_at,
                   i.labels, i.state
            FROM issues i JOIN repos r ON r.id = i.repo_id
            WHERE i.kind = 'issue'
            ORDER BY i.created_at DESC LIMIT %s
            """,
            (min(limit, 200),),
        )
        rows: list[Any] = await cur.fetchall()
    by_issue = {(r.repo, r.number): r for r in await runs.recent(limit=500)}
    out = []
    for row in rows:
        run = by_issue.get((row["repo"], row["number"]))
        out.append(
            {
                **{k: row[k] for k in ("repo", "number", "title", "url", "labels", "state")},
                "created_at": row["created_at"].isoformat(),
                "run": run_summary(run) if run else None,
            }
        )
    return out


@router.get("/issues/{owner}/{name}/{number}")
async def issue_thread(
    owner: str, name: str, number: int, request: Request, runs: Runs
) -> dict[str, Any]:
    from indexing.corpus import CorpusReader

    repo = f"{owner}/{name}"
    found = await CorpusReader(await _pool(request)).issue(repo, number)
    if found is None:
        raise HTTPException(404, "issue not ingested")
    issue, comments = found
    run = next(
        (r for r in await runs.recent(limit=500) if r.repo == repo and r.number == number), None
    )
    return {
        "repo": repo,
        "issue": issue.model_dump(mode="json"),
        "comments": [c.model_dump(mode="json") for c in comments],
        "run": RunOut.of(run).model_dump(mode="json") if run else None,
    }


async def run_events(
    runs: RunStore, *, interval: float, once: bool = False
) -> AsyncGenerator[dict[str, str]]:
    """Emit a `run` event whenever a run's status or step count changes."""
    seen: dict[str, tuple[str, int]] = {}
    while True:
        for run in reversed(await runs.recent(limit=100)):
            fingerprint = (run.status, len(run.steps))
            if seen.get(run.run_id) != fingerprint:
                seen[run.run_id] = fingerprint
                yield {"event": "run", "data": json.dumps(run_summary(run))}
        if once:
            return
        await asyncio.sleep(interval)


@router.get("/stream/runs")
async def stream_runs(runs: Runs) -> EventSourceResponse:
    return EventSourceResponse(run_events(runs, interval=STREAM_INTERVAL_S), ping=15)


async def consumer_lag(bootstrap: str, groups: list[str]) -> list[dict[str, Any]]:
    from aiokafka import AIOKafkaConsumer, TopicPartition
    from aiokafka.admin import AIOKafkaAdminClient

    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap)
    probe = AIOKafkaConsumer(bootstrap_servers=bootstrap)
    await admin.start()
    await probe.start()
    try:
        out: list[dict[str, Any]] = []
        for group in groups:
            committed: dict[TopicPartition, Any] = await admin.list_consumer_group_offsets(group)
            if not committed:
                out.append({"group": group, "topic": None, "lag": None})
                continue
            ends = await probe.end_offsets(list(committed))
            per_topic: dict[str, int] = {}
            for tp, meta in committed.items():
                per_topic[tp.topic] = per_topic.get(tp.topic, 0) + max(0, ends[tp] - meta.offset)
            out += [
                {"group": group, "topic": t, "lag": lag} for t, lag in sorted(per_topic.items())
            ]
        return out
    finally:
        await probe.stop()
        await admin.close()


@router.get("/ops")
async def ops(request: Request) -> dict[str, Any]:
    from common.config import get_settings

    settings = get_settings()
    pool = await _pool(request)
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT status, count(*) AS n FROM triage_runs GROUP BY status")
        statuses: list[Any] = await cur.fetchall()
        cur = await conn.execute(
            """SELECT count(*) FILTER (WHERE a.decision IN ('approve', 'edit')) AS approved,
                      count(*) FILTER (WHERE a.decision = 'edit') AS edited,
                      count(*) AS decided
               FROM approvals a"""
        )
        decisions: Any = await cur.fetchone()
        cur = await conn.execute(
            """SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50,
                      percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95
               FROM triage_runs WHERE latency_ms IS NOT NULL"""
        )
        latency: Any = await cur.fetchone()
    try:
        lag = await asyncio.wait_for(
            consumer_lag(settings.kafka_bootstrap, ["opspilot-indexer", "opspilot-triage"]), 5
        )
    except Exception as exc:
        lag = [{"group": "unavailable", "topic": None, "lag": None, "error": str(exc)[:200]}]
    decided = decisions["decided"] or 0
    return {
        "runs_by_status": {row["status"]: row["n"] for row in statuses},
        "approval_rate": decisions["approved"] / decided if decided else None,
        "edit_rate": decisions["edited"] / decided if decided else None,
        "latency_ms": {"p50": latency["p50"], "p95": latency["p95"]},
        "consumer_lag": lag,
        "links": {
            "temporal": "http://localhost:8233",
            "phoenix": "http://localhost:6006",
            "redpanda_console": "http://localhost:8088",
        },
    }
