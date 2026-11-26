"""Eval reports produced by `make eval` / `make redteam` (JSON files in docs/eval)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api", tags=["evals"])
EVAL_DIR = Path("docs/eval")


def _reports(kind: str) -> list[Path]:
    if not EVAL_DIR.exists():
        return []
    files = sorted(EVAL_DIR.glob("*.json"), reverse=True)
    if kind == "triage":
        return [p for p in files if p.stem.count("-") == 2]  # YYYY-MM-DD.json
    return [p for p in files if p.stem.endswith(f"-{kind}")]


@router.get("/evals")
def list_reports() -> dict[str, list[str]]:
    return {kind: [p.stem for p in _reports(kind)] for kind in ("triage", "retrieval", "redteam")}


@router.get("/evals/{kind}/latest")
def latest(kind: str) -> dict[str, Any]:
    if kind not in ("triage", "retrieval", "redteam"):
        raise HTTPException(404, "unknown report kind")
    reports = _reports(kind)
    if not reports:
        raise HTTPException(404, f"no {kind} report yet: run `make eval` / `make redteam`")
    data: dict[str, Any] = json.loads(reports[0].read_text(encoding="utf-8"))
    data["file"] = reports[0].name
    return data
