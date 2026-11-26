"""Persistence of triage runs, suggestions, approvals and human-edit ground truth."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

SUGGESTION_KINDS = (
    "classification",
    "labels",
    "duplicates",
    "draft",
    "repro_checklist",
    "verification",
)


@dataclass
class RunRecord:
    run_id: str
    repo: str
    number: int
    model: str
    prompt_version: str
    retrieval_variant: str
    status: str
    workflow_id: str | None = None
    title: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    suggestions: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    latency_ms: int | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    decision: dict[str, Any] | None = None


def suggestions_from_state(state: dict[str, Any]) -> dict[str, Any]:
    classification = state.get("classification") or {}
    draft = state.get("draft") or {}
    return {
        "classification": classification or None,
        "labels": classification.get("labels", []),
        "duplicates": state.get("duplicates", []),
        "draft": draft or None,
        "repro_checklist": draft.get("repro_checklist", []),
        "verification": state.get("verification", {}),
        "evidence": [
            {k: v for k, v in e.items() if k != "text"} for e in state.get("evidence", [])
        ],
        "injection_flags": state.get("injection_flags", []),
        "needs_human": state.get("needs_human", False),
        "errors": state.get("errors", []),
    }


class RunStore(Protocol):
    async def create(self, run: RunRecord) -> None: ...

    async def awaiting_approval(
        self,
        run_id: str,
        *,
        steps: list[dict[str, Any]],
        suggestions: dict[str, Any],
        latency_ms: int,
        needs_human: bool,
    ) -> None: ...

    async def fail(self, run_id: str, error: str) -> None: ...

    async def decide(
        self, run_id: str, *, decision: dict[str, Any], status: str, applied_url: str | None
    ) -> None: ...

    async def get(self, run_id: str) -> RunRecord | None: ...

    async def recent(self, *, status: str | None = None, limit: int = 50) -> list[RunRecord]: ...


@dataclass
class MemoryRunStore:
    runs: dict[str, RunRecord] = field(default_factory=dict)
    ground_truth: list[dict[str, Any]] = field(default_factory=list)

    async def create(self, run: RunRecord) -> None:
        self.runs[run.run_id] = run

    async def awaiting_approval(
        self,
        run_id: str,
        *,
        steps: list[dict[str, Any]],
        suggestions: dict[str, Any],
        latency_ms: int,
        needs_human: bool,
    ) -> None:
        run = self.runs[run_id]
        run.steps, run.suggestions, run.latency_ms = steps, suggestions, latency_ms
        run.status = "needs_human" if needs_human else "awaiting_approval"

    async def fail(self, run_id: str, error: str) -> None:
        self.runs[run_id].status, self.runs[run_id].error = "failed", error

    async def decide(
        self, run_id: str, *, decision: dict[str, Any], status: str, applied_url: str | None
    ) -> None:
        run = self.runs[run_id]
        run.status, run.decision = status, {**decision, "applied_url": applied_url}
        if decision.get("decision") == "edit" and decision.get("edited"):
            self.ground_truth.append(
                {"repo": run.repo, "number": run.number, "payload": decision["edited"]}
            )

    async def get(self, run_id: str) -> RunRecord | None:
        return self.runs.get(run_id)

    async def recent(self, *, status: str | None = None, limit: int = 50) -> list[RunRecord]:
        runs = [r for r in self.runs.values() if status is None or r.status == status]
        return sorted(runs, key=lambda r: r.started_at, reverse=True)[:limit]


class PgRunStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def create(self, run: RunRecord) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO triage_runs (id, issue_id, workflow_id, model, prompt_version,
                                         retrieval_variant, status, started_at)
                SELECT %s, i.id, %s, %s, %s, %s, 'running', %s
                FROM issues i JOIN repos r ON r.id = i.repo_id
                WHERE r.full_name = %s AND i.number = %s
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    run.run_id,
                    run.workflow_id,
                    run.model,
                    run.prompt_version,
                    run.retrieval_variant,
                    run.started_at,
                    run.repo,
                    run.number,
                ),
            )

    async def awaiting_approval(
        self,
        run_id: str,
        *,
        steps: list[dict[str, Any]],
        suggestions: dict[str, Any],
        latency_ms: int,
        needs_human: bool,
    ) -> None:
        status = "needs_human" if needs_human else "awaiting_approval"
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(
                "UPDATE triage_runs SET status = %s, steps = %s, latency_ms = %s WHERE id = %s",
                (status, Jsonb(steps), latency_ms, run_id),
            )
            await conn.execute("DELETE FROM suggestions WHERE run_id = %s", (run_id,))
            for kind in SUGGESTION_KINDS:
                payload = suggestions.get(kind)
                if payload is None:
                    continue
                confidence = (
                    suggestions["classification"].get("confidence")
                    if kind == "classification" and suggestions.get("classification")
                    else None
                )
                await conn.execute(
                    "INSERT INTO suggestions (run_id, kind, payload, confidence) "
                    "VALUES (%s, %s, %s, %s)",
                    (
                        run_id,
                        kind,
                        Jsonb(
                            {
                                "value": payload,
                                **(
                                    {
                                        "extra": {
                                            "evidence": suggestions.get("evidence", []),
                                            "injection_flags": suggestions.get(
                                                "injection_flags", []
                                            ),
                                            "errors": suggestions.get("errors", []),
                                        }
                                    }
                                    if kind == "verification"
                                    else {}
                                ),
                            }
                        ),
                        confidence,
                    ),
                )

    async def fail(self, run_id: str, error: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE triage_runs SET status = 'failed', error = %s, finished_at = now() "
                "WHERE id = %s",
                (error[:2000], run_id),
            )

    async def decide(
        self, run_id: str, *, decision: dict[str, Any], status: str, applied_url: str | None
    ) -> None:
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO approvals
                   (run_id, decision, reviewer, edited_payload, note, applied_url)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    run_id,
                    decision["decision"],
                    decision.get("reviewer", "unknown"),
                    Jsonb(decision.get("edited")) if decision.get("edited") else None,
                    decision.get("note"),
                    applied_url,
                ),
            )
            await conn.execute(
                "UPDATE triage_runs SET status = %s, finished_at = now() WHERE id = %s",
                (status, run_id),
            )
            if decision["decision"] == "edit" and decision.get("edited"):
                await conn.execute(
                    """INSERT INTO ground_truth (issue_id, source, payload)
                       SELECT issue_id, 'human_edit', %s FROM triage_runs WHERE id = %s
                       ON CONFLICT (issue_id, source) DO UPDATE SET payload = EXCLUDED.payload,
                           created_at = now()""",
                    (Jsonb(decision["edited"]), run_id),
                )

    async def get(self, run_id: str) -> RunRecord | None:
        runs = await self._query("WHERE t.id = %s", (run_id,), 1)
        return runs[0] if runs else None

    async def recent(self, *, status: str | None = None, limit: int = 50) -> list[RunRecord]:
        if status:
            return await self._query("WHERE t.status = %s", (status,), limit)
        return await self._query("", (), limit)

    async def _query(self, where: str, params: tuple[Any, ...], limit: int) -> list[RunRecord]:
        sql = f"""
            SELECT t.*, r.full_name AS repo, i.number, i.title,
                   (SELECT jsonb_object_agg(s.kind, s.payload) FROM suggestions s
                     WHERE s.run_id = t.id) AS sugg,
                   (SELECT to_jsonb(a) FROM approvals a WHERE a.run_id = t.id
                     ORDER BY a.decided_at DESC LIMIT 1) AS approval
            FROM triage_runs t JOIN issues i ON i.id = t.issue_id JOIN repos r ON r.id = i.repo_id
            {where} ORDER BY t.started_at DESC LIMIT {int(limit)}
        """  # noqa: S608 - where/limit are built from constants and ints
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql.encode(), params)
            rows: list[Any] = await cur.fetchall()
        out: list[RunRecord] = []
        for row in rows:
            raw = row["sugg"] or {}
            suggestions: dict[str, Any] = {k: v.get("value") for k, v in raw.items()}
            extra = (raw.get("verification") or {}).get("extra", {})
            suggestions.update(extra)
            out.append(
                RunRecord(
                    run_id=str(row["id"]),
                    repo=row["repo"],
                    number=row["number"],
                    model=row["model"],
                    prompt_version=row["prompt_version"],
                    retrieval_variant=row["retrieval_variant"],
                    status=row["status"],
                    workflow_id=row["workflow_id"],
                    title=row["title"],
                    steps=row["steps"] or [],
                    suggestions=suggestions,
                    error=row["error"],
                    latency_ms=row["latency_ms"],
                    started_at=row["started_at"],
                    decision=row["approval"],
                )
            )
        return out
