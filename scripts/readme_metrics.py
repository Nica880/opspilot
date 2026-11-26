"""Write the README metrics section from the latest reports in docs/eval (never by hand).

Usage: uv run python scripts/readme_metrics.py   (or `make readme-metrics`)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "docs" / "eval"
README = ROOT / "README.md"
START, END = "<!-- metrics:start -->", "<!-- metrics:end -->"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def latest(suffix: str) -> tuple[str, dict[str, Any]] | None:
    files = sorted(EVAL_DIR.glob("*.json"), reverse=True)
    for path in files:
        stem = path.stem
        if (suffix == "" and _DATE.match(stem)) or (suffix and stem.endswith(f"-{suffix}")):
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            return path.name, data
    return None


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def render(
    triage: tuple[str, dict[str, Any]] | None,
    retrieval: tuple[str, dict[str, Any]] | None,
    redteam: tuple[str, dict[str, Any]] | None,
) -> str:
    out: list[str] = []
    if triage:
        name, data = triage
        meta = data["meta"]
        report = f"docs/eval/{name.replace('.json', '.md')}"
        out += [
            f"**Triage** ([{report}]({report}), {meta.get('evaluated per config')} held-out "
            f"issues per config, cutoff {meta.get('cutoff')}, {meta.get('hardware')}):",
            "",
            "| Model / prompt / retrieval | Label F1 micro | Dup recall@5 | Groundedness (1-5) | "
            "Citations correct | p50 / p95 latency | Issues/hour |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in data["results"]:
            c = r["config"]
            grounded = "n/a" if r["groundedness"] is None else f"{r['groundedness']:.2f}"
            out.append(
                f"| {c['model']} / {c['prompt_version']} / {c['retrieval_variant']} "
                f"| {r['label_micro_f1']:.3f} | {_pct(r['dup_recall_at_5'])} | {grounded} "
                f"| {_pct(r['citations_correct'])} | {r['p50_ms'] / 1000:.1f} s / "
                f"{r['p95_ms'] / 1000:.1f} s | {r['issues_per_hour']:.0f} |"
            )
        out.append("")
    else:
        out += [
            "**Triage quality:** not measured yet. Run `make eval` on the Mac (see "
            "[ADR-008](docs/adr/0008-evaluation.md)); this section is regenerated from the "
            "report by `make readme-metrics`.",
            "",
        ]
    if retrieval:
        name, data = retrieval
        out += [
            f"**Duplicate retrieval** ([docs/eval/{name.replace('.json', '.md')}]"
            f"(docs/eval/{name.replace('.json', '.md')}), {data['meta'].get('cases')} cases):",
            "",
            "| Variant | recall@1 | recall@5 | MRR |",
            "|---|---|---|---|",
        ]
        for r in data["results"]:
            out.append(
                f"| {r['variant']} | {_pct(r['recall']['1'])} | {_pct(r['recall']['5'])} "
                f"| {r['mrr']:.3f} |"
            )
        out.append("")
    else:
        out += ["**Duplicate retrieval:** not measured yet (`make eval-retrieval`).", ""]
    if redteam:
        name, data = redteam
        s, meta = data["summary"], data["meta"]
        out += [
            f"**Red team** ([docs/eval/{name}](docs/eval/{name})): {s['passed']}/{s['cases']} "
            f"attacks contained with LLM provider `{meta.get('llm_provider')}`"
            + (
                " (deterministic fake: measures the guardrails, not a model)."
                if meta.get("llm_provider") == "fake"
                else f", model `{meta.get('model')}`."
            ),
            "",
        ]
    return "\n".join(out).rstrip() + "\n"


def main() -> None:
    text = README.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit("README has no metrics markers")
    body = render(latest(""), latest("retrieval"), latest("redteam"))
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    README.write_text(f"{head}{START}\n{body}{END}{tail}", encoding="utf-8", newline="\n")
    print("README metrics updated")


if __name__ == "__main__":
    main()
