from __future__ import annotations

import json

from agent.runs import MemoryRunStore, RunRecord
from api.routes_ui import run_events, run_summary


async def test_run_stream_emits_only_changes() -> None:
    store = MemoryRunStore()
    store.runs["a"] = RunRecord("a", "o/r", 1, "m", "v1", "hybrid", "running", title="t")
    store.runs["b"] = RunRecord("b", "o/r", 2, "m", "v1", "hybrid", "applied", title="u")

    gen = run_events(store, interval=0)
    first = {json.loads((await anext(gen))["data"])["run_id"] for _ in range(2)}
    assert first == {"a", "b"}

    store.runs["a"].status = "awaiting_approval"
    store.runs["a"].steps = [{"node": "sanitize", "ms": 1, "summary": ""}]
    changed = json.loads((await anext(gen))["data"])
    assert changed["run_id"] == "a" and changed["status"] == "awaiting_approval"
    assert changed["steps"] == ["sanitize"]
    await gen.aclose()


def test_run_summary_is_compact() -> None:
    run = RunRecord("a", "o/r", 1, "m", "v1", "hybrid", "awaiting_approval", title="t")
    run.suggestions = {"classification": {"type": "bug", "severity": "high"}, "labels": ["bug"]}
    summary = run_summary(run)
    assert summary["type"] == "bug" and summary["labels"] == ["bug"]
    assert "suggestions" not in summary
