"""Time-split triage evaluation and the report in docs/eval/<date>.md.

    index: everything ingested (the retrieval filter hides anything created after the cutoff)
    eval:  issues created at/after the cutoff ("held out"), never seen as evidence for themselves

Metrics per configuration (model x prompt version x retrieval variant):
labels micro/macro F1 vs maintainer labels, duplicate recall@5/MRR, linked-answer recall@k,
LLM-as-judge groundedness/citation correctness (cross-judged), refusal rate, p50/p95 latency,
issues/hour, cost.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import subprocess
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from agent.graph import TriageDeps, build_graph
from agent.llm import LLMProvider, StructuredLLM
from agent.runs import MemoryRunStore
from agent.service import IssueInput, TriageConfig, TriageService
from agent.taxonomy import IGNORED_LABELS, top_labels
from evals.ground_truth import find_duplicate_target, linked_answers
from evals.judge import judge, judge_model_for
from evals.metrics import label_f1, mrr, percentile, rank_of, recall_at_k
from indexing.chunking import doc_key_issue
from indexing.qdrant_index import SearchFilters
from indexing.search import SearchService, Variant
from ingestion.models import CommentRecord, IssueRecord

log = logging.getLogger(__name__)

GITHUB_LINK_PREFIX = "https://github.com/"


@dataclass(frozen=True)
class EvalConfig:
    model: str
    prompt_version: str
    retrieval_variant: str

    @property
    def name(self) -> str:
        return f"{self.model} / {self.prompt_version} / {self.retrieval_variant}"


@dataclass
class HeldOut:
    repo: str
    issue: IssueRecord
    comments: list[CommentRecord]
    gold_labels: set[str]
    duplicate_of: int | None
    linked: list[str]


@dataclass
class Split:
    cutoff: datetime
    train_size: int
    held_out: list[HeldOut]
    vocab: dict[str, list[str]]


@dataclass
class ConfigResult:
    config: EvalConfig
    n: int
    label_micro_f1: float
    label_macro_f1: float
    label_n: int
    dup_recall_at_5: float
    dup_mrr: float
    dup_n: int
    dup_flag_precision: float | None
    linked_recall_at_6: float
    linked_n: int
    refusal_rate: float
    verified_rate: float
    needs_human_rate: float
    judge_n: int
    groundedness: float | None
    citations_correct: float | None
    p50_ms: float
    p95_ms: float
    issues_per_hour: float
    cost_usd: float = 0.0
    failures: int = 0
    examples: list[dict[str, Any]] = field(default_factory=list)


def build_split(
    threads: Sequence[tuple[str, IssueRecord, list[CommentRecord]]],
    *,
    cutoff: datetime | None = None,
    cutoff_quantile: float = 0.8,
) -> Split:
    issues = [t for t in threads if t[1].kind == "issue"]
    if not issues:
        raise ValueError("no issues ingested")
    if cutoff is None:
        dates = sorted(t[1].created_at for t in issues)
        cutoff = dates[min(len(dates) - 1, int(len(dates) * cutoff_quantile))]
    train = [t for t in issues if t[1].created_at < cutoff]
    vocab: dict[str, list[str]] = {}
    for repo in {t[0] for t in issues}:
        vocab[repo] = top_labels([t[1].labels for t in train if t[0] == repo])
    held: list[HeldOut] = []
    for repo, issue, comments in issues:
        if issue.created_at < cutoff:
            continue
        held.append(
            HeldOut(
                repo=repo,
                issue=issue,
                comments=comments,
                gold_labels={lab for lab in issue.labels if lab not in IGNORED_LABELS},
                duplicate_of=find_duplicate_target(issue, comments),
                linked=linked_answers(comments),
            )
        )
    return Split(cutoff=cutoff, train_size=len(train), held_out=held, vocab=vocab)


def _norm_url(url: str) -> str:
    return url.split("#")[0].rstrip("/").lower()


def linked_rank(link: str, evidence: list[dict[str, Any]], repo: str) -> int | None:
    """Rank of a maintainer-linked doc page or PR/issue among the evidence items."""
    if link.startswith(GITHUB_LINK_PREFIX):
        parts = link.removeprefix(GITHUB_LINK_PREFIX).split("/")
        if len(parts) >= 4 and f"{parts[0]}/{parts[1]}" == repo:
            return rank_of(doc_key_issue(repo, int(parts[3])), [e["doc_key"] for e in evidence])
        return None
    return rank_of(_norm_url(link), [_norm_url(str(e.get("url", ""))) for e in evidence])


async def duplicate_ranks(
    search: SearchService, held: list[HeldOut], variant: Variant, cutoff: datetime
) -> list[int | None]:
    ranks: list[int | None] = []
    for item in held:
        if item.duplicate_of is None:
            continue
        hits = await search.search(
            f"{item.issue.title}\n\n{item.issue.body}",
            variant=variant,
            k=5,
            filters=SearchFilters(
                repos=[item.repo],
                kinds=("issue", "pull_request", "discussion"),
                created_before=min(item.issue.created_at, cutoff),
                exclude_doc_keys=[doc_key_issue(item.repo, item.issue.number)],
            ),
        )
        ranks.append(
            rank_of(doc_key_issue(item.repo, item.duplicate_of), [h.doc_key for h in hits])
        )
    return ranks


async def run_config(
    config: EvalConfig,
    split: Split,
    held: list[HeldOut],
    *,
    search: SearchService,
    provider_for: Callable[[str], LLMProvider],
    judge_sample: int,
    seed: int = 7,
) -> ConfigResult:
    provider = provider_for(config.model)
    deps = TriageDeps(llm=StructuredLLM(provider), search=search)
    service = TriageService(
        build_graph(deps, InMemorySaver()),
        MemoryRunStore(),
        TriageConfig(config.model, config.prompt_version, config.retrieval_variant),
        labels_by_repo=split.vocab,
    )
    latencies: list[float] = []
    label_pairs: list[tuple[set[str], set[str]]] = []
    vocab_all: set[str] = set()
    linked: list[int | None] = []
    flagged = correct_flags = refusals = verified = needs_human = failures = 0
    judged: list[tuple[HeldOut, dict[str, Any]]] = []
    started_all = time.perf_counter()
    for item in held:
        issue = item.issue
        started = time.perf_counter()
        try:
            record = await service.start(
                IssueInput(
                    repo=item.repo,
                    number=issue.number,
                    title=issue.title,
                    body=issue.body,
                    url=issue.html_url,
                    created_at=issue.created_at.isoformat(),
                    knowledge_cutoff=split.cutoff.isoformat(),
                ),
                run_id=str(uuid.uuid4()),
            )
        except Exception as exc:
            log.warning("triage failed for %s#%s: %s", item.repo, issue.number, exc)
            failures += 1
            continue
        latencies.append((time.perf_counter() - started) * 1000)
        s = record.suggestions
        vocab = set(split.vocab.get(item.repo, []))
        vocab_all |= vocab
        if item.gold_labels & vocab:
            label_pairs.append((item.gold_labels & vocab, set(s.get("labels") or [])))
        for dup in s.get("duplicates") or []:
            if dup.get("is_duplicate") and dup.get("confidence", 0) >= 0.6:
                flagged += 1
                correct_flags += int(dup.get("number") == item.duplicate_of)
        evidence = s.get("evidence") or []
        for link in item.linked:
            linked.append(linked_rank(link, evidence, item.repo))
        draft = s.get("draft") or {}
        refusals += int(not draft.get("can_answer", False))
        verified += int((s.get("verification") or {}).get("ok", False))
        needs_human += int(record.status == "needs_human")
        if draft:
            run_state = await service.graph.aget_state(
                {"configurable": {"thread_id": record.run_id}}
            )
            judged.append(
                (item, {"draft": draft, "evidence": run_state.values.get("evidence", [])})
            )
    wall_hours = (time.perf_counter() - started_all) / 3600
    n = len(latencies)

    grounded: list[int] = []
    cites: list[bool] = []
    if judge_sample > 0 and judged:
        judge_llm = StructuredLLM(provider_for(judge_model_for(config.model)))
        rng = random.Random(seed)
        for item, payload in rng.sample(judged, min(judge_sample, len(judged))):
            verdict = await judge(
                judge_llm, item.issue.title, payload["draft"], payload["evidence"]
            )
            if verdict is not None:
                grounded.append(verdict.groundedness)
                cites.append(verdict.citations_correct)

    f1 = label_f1(label_pairs, vocab_all)
    dup = await duplicate_ranks(search, held, config.retrieval_variant, split.cutoff)  # type: ignore[arg-type]
    return ConfigResult(
        config=config,
        n=n,
        label_micro_f1=f1.micro,
        label_macro_f1=f1.macro,
        label_n=f1.n,
        dup_recall_at_5=recall_at_k(dup, 5),
        dup_mrr=mrr(dup),
        dup_n=len(dup),
        dup_flag_precision=correct_flags / flagged if flagged else None,
        linked_recall_at_6=recall_at_k(linked, 6),
        linked_n=len(linked),
        refusal_rate=refusals / n if n else 0.0,
        verified_rate=verified / n if n else 0.0,
        needs_human_rate=needs_human / n if n else 0.0,
        judge_n=len(grounded),
        groundedness=sum(grounded) / len(grounded) if grounded else None,
        citations_correct=sum(cites) / len(cites) if cites else None,
        p50_ms=percentile(latencies, 0.5),
        p95_ms=percentile(latencies, 0.95),
        issues_per_hour=n / wall_hours if wall_hours > 0 else 0.0,
        failures=failures,
    )


def _fmt(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def to_markdown(results: list[ConfigResult], meta: dict[str, Any]) -> str:
    lines = [
        f"# OpsPilot eval report ({meta['date']})",
        "",
        "Generated by `make eval` (`python -m evals.harness`). Every number below was measured by",
        "that command on the data described here.",
        "",
        "| Setting | Value |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in meta.items()]
    lines += [
        "",
        "## Triage quality",
        "",
        "| Config | n | label micro-F1 | label macro-F1 | dup recall@5 | dup MRR | linked recall@6 "
        "| groundedness (1-5) | citations correct | refusal rate | verified |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.config.name} | {r.n} | {_fmt(r.label_micro_f1)} | {_fmt(r.label_macro_f1)} "
            f"| {_fmt(r.dup_recall_at_5)} | {_fmt(r.dup_mrr)} | {_fmt(r.linked_recall_at_6)} "
            f"| {_fmt(r.groundedness, 2)} | {_fmt(r.citations_correct)} | {_fmt(r.refusal_rate)} "
            f"| {_fmt(r.verified_rate)} |"
        )
    lines += [
        "",
        "Support: labels on `label n` issues with at least one in-vocabulary maintainer label;",
        "duplicates on `dup n` held-out issues closed with `Duplicate of #N`; linked answers on",
        "`linked n` maintainer links to docs/PRs; judge on `judge n` sampled drafts",
        "(cross-judged).",
        "",
        "| Config | label n | dup n | linked n | judge n | dup flag precision | needs human "
        "| failures |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.config.name} | {r.label_n} | {r.dup_n} | {r.linked_n} | {r.judge_n} "
            f"| {_fmt(r.dup_flag_precision)} | {_fmt(r.needs_human_rate)} | {r.failures} |"
        )
    lines += [
        "",
        "## Operations",
        "",
        "| Config | p50 latency | p95 latency | issues/hour | cost |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.config.name} | {r.p50_ms / 1000:.1f} s | {r.p95_ms / 1000:.1f} s "
            f"| {r.issues_per_hour:.0f} | ${r.cost_usd:.2f} |"
        )
    return "\n".join(lines) + "\n"


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def write_report(results: list[ConfigResult], meta: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{meta['date']}.md"
    path.write_text(to_markdown(results, meta), encoding="utf-8")
    path.with_suffix(".json").write_text(
        json.dumps({"meta": meta, "results": [asdict(r) for r in results]}, indent=2, default=str),
        encoding="utf-8",
    )
    return path


async def save_eval_runs(results: list[ConfigResult], meta: dict[str, Any]) -> None:
    from psycopg.types.json import Jsonb

    from common.db import get_pool

    pool = await get_pool()
    async with pool.connection() as conn:
        for r in results:
            metrics = {k: v for k, v in asdict(r).items() if k not in ("config", "examples")}
            await conn.execute(
                """INSERT INTO eval_runs (id, suite, model, prompt_version, retrieval_variant,
                                          cutoff, n_cases, metrics, git_sha, finished_at)
                   VALUES (%s, 'triage', %s, %s, %s, %s, %s, %s, %s, now())""",
                (
                    str(uuid.uuid4()),
                    r.config.model,
                    r.config.prompt_version,
                    r.config.retrieval_variant,
                    meta["cutoff"],
                    r.n,
                    Jsonb(metrics),
                    meta["git_sha"],
                ),
            )


async def main_async(args: argparse.Namespace) -> tuple[list[ConfigResult], dict[str, Any]]:
    from agent.runtime import build_provider
    from common.config import get_settings
    from common.db import close_pool, get_pool
    from indexing.corpus import CorpusReader
    from indexing.factory import build_search

    settings = get_settings()
    search = await build_search(settings)
    reader = CorpusReader(await get_pool())
    threads = [t async for t in reader.iter_issues()]
    split = build_split(
        threads, cutoff=datetime.fromisoformat(args.cutoff) if args.cutoff else None
    )
    rng = random.Random(args.seed)
    held = sorted(split.held_out, key=lambda h: (h.repo, h.issue.number))
    sample = rng.sample(held, min(args.limit, len(held)))
    configs = [
        EvalConfig(m, p, v) for m in args.models for p in args.prompts for v in args.variants
    ]
    results: list[ConfigResult] = []
    for cfg in configs:
        log.info("evaluating %s on %d issues", cfg.name, len(sample))
        results.append(
            await run_config(
                cfg,
                split,
                sample,
                search=search,
                provider_for=lambda model: build_provider(settings, model),
                judge_sample=args.judge_sample,
                seed=args.seed,
            )
        )
    meta = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "git_sha": git_sha(),
        "mode": settings.mode,
        "llm_provider": settings.llm_provider,
        "repos": ", ".join(settings.repos),
        "cutoff": split.cutoff.isoformat(),
        "issues before cutoff (index, label vocabulary)": split.train_size,
        "held-out issues (after cutoff)": len(split.held_out),
        "evaluated per config": len(sample),
        "judge sample per config": args.judge_sample,
        "embedder": settings.embed_model if settings.llm_provider != "fake" else "hashing (fake)",
        "hardware": args.hardware,
    }
    try:
        await save_eval_runs(results, meta)
    finally:
        await close_pool()
    return results, meta


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="evals.harness")
    parser.add_argument("--models", nargs="*", default=["qwen2.5:7b-instruct", "llama3.1:8b"])
    parser.add_argument("--prompts", nargs="*", default=["v1", "v2"])
    parser.add_argument("--variants", nargs="*", default=["dense", "hybrid", "hybrid_rerank"])
    parser.add_argument("--limit", type=int, default=50, help="held-out issues per config")
    parser.add_argument("--judge-sample", type=int, default=20)
    parser.add_argument("--cutoff", help="ISO date; default = 80th percentile of creation dates")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--hardware", default="Apple M3 Pro, 36 GB, Ollama on host (Metal)")
    parser.add_argument("--out", type=Path, default=Path("docs/eval"))
    args = parser.parse_args(argv)
    results, meta = asyncio.run(main_async(args))
    print(f"wrote {write_report(results, meta, args.out)}")


if __name__ == "__main__":
    main()
