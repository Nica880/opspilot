"""The triage agent as an explicit LangGraph state machine.

sanitize -> classify -> retrieve -> dedupe -> draft -> verify --(retry once)--> draft
                                                        verify --> approval (interrupt) -> act
Any node may fail softly: it records the error, sets `needs_human`, and the run still reaches the
approval queue with whatever was produced, so a human always gets the final say.
"""

from __future__ import annotations

import operator
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from opentelemetry import trace

from agent.actions import SandboxActions
from agent.guardrails import redact, sanitize, wrap_untrusted
from agent.llm import Message, StructuredLLM, StructuredOutputError
from agent.prompts import load_prompt
from agent.schemas import Classification, CriticVerdict, Draft, DuplicateVerdict
from agent.verify import verify_classification, verify_draft
from indexing.chunking import doc_key_issue
from indexing.qdrant_index import SearchFilters
from indexing.search import SearchService, Variant

tracer = trace.get_tracer("opspilot.agent")

MAX_EVIDENCE = 6
DUPLICATE_CANDIDATES = 3
DUPLICATE_MIN_CONFIDENCE = 0.6
MAX_DRAFT_ATTEMPTS = 2
ISSUE_KINDS = ("issue", "pull_request", "discussion")
EVIDENCE_CHARS = 1200
ASK_FOR_INFO = (
    "Thanks for the report! Could you share a minimal reproducible example, the versions you use "
    "and the full traceback, so we can look into it?"
)


class Step(TypedDict):
    node: str
    ms: int
    summary: str


class TriageState(TypedDict, total=False):
    # input
    run_id: str
    repo: str
    number: int
    title: str
    body: str
    url: str
    created_at: str
    prompt_version: str
    retrieval_variant: str
    allowed_labels: list[str]
    areas: list[str]
    # produced
    sanitized: str
    injection_flags: list[str]
    classification: dict[str, Any] | None
    evidence: list[dict[str, Any]]
    duplicate_candidates: list[dict[str, Any]]
    duplicates: list[dict[str, Any]]
    draft: dict[str, Any] | None
    draft_attempts: int
    draft_feedback: list[str]
    verification: dict[str, Any]
    needs_human: bool
    errors: Annotated[list[str], operator.add]
    steps: Annotated[list[Step], operator.add]
    decision: dict[str, Any] | None
    applied: dict[str, Any] | None
    status: str


@dataclass
class TriageDeps:
    llm: StructuredLLM
    search: SearchService
    actions: SandboxActions | None = None
    llm_critic: bool = False


Node = Callable[[TriageState], Awaitable[dict[str, Any]]]


def timed(name: str, kind: str = "CHAIN") -> Callable[[Node], Node]:
    """Wrap a node: OpenTelemetry span (OpenInference kind) + a step record for the UI/SSE."""

    def decorator(fn: Node) -> Node:
        async def wrapper(state: TriageState) -> dict[str, Any]:
            started = time.perf_counter()
            with tracer.start_as_current_span(f"triage.{name}") as span:
                span.set_attribute("openinference.span.kind", kind)
                span.set_attribute("opspilot.run_id", state.get("run_id", ""))
                update = await fn(state)
                summary = str(update.pop("_summary", ""))
                span.set_attribute("opspilot.summary", summary)
            step: Step = {
                "node": name,
                "ms": int((time.perf_counter() - started) * 1000),
                "summary": summary,
            }
            return {**update, "steps": [step]}

        return wrapper

    return decorator


def _issue_block(state: TriageState) -> str:
    return wrap_untrusted("issue", f"Title: {state['title']}\n\n{state.get('sanitized', '')}")


def build_graph(
    deps: TriageDeps, checkpointer: BaseCheckpointSaver[Any] | None = None
) -> CompiledStateGraph[TriageState, None, TriageState, TriageState]:
    @timed("sanitize", "GUARDRAIL")
    async def sanitize_node(state: TriageState) -> dict[str, Any]:
        result = sanitize(f"{state['body']}")
        title_scan = sanitize(state["title"]).report.flags
        flags = sorted(set(result.report.flags) | set(title_scan))
        return {
            "sanitized": result.text,
            "injection_flags": flags,
            "needs_human": False,
            "draft_attempts": 0,
            "draft_feedback": [],
            "_summary": f"flags={flags}" if flags else "clean",
        }

    @timed("classify", "LLM")
    async def classify_node(state: TriageState) -> dict[str, Any]:
        system = load_prompt(
            state["prompt_version"],
            "classify",
            repo=state["repo"],
            areas=", ".join(state["areas"]),
            labels=", ".join(state["allowed_labels"]),
        )
        try:
            result = await deps.llm.generate(
                "classify",
                [Message.of("system", system), Message.of("user", _issue_block(state))],
                Classification,
            )
        except StructuredOutputError as exc:
            return {
                "classification": None,
                "needs_human": True,
                "errors": [str(exc)],
                "_summary": "failed",
            }
        # Clamp to the allowed vocabularies; the verifier still reports what was dropped.
        data = result.model_dump()
        data["proposed_labels_raw"] = list(result.labels)
        data["labels"] = [label for label in result.labels if label in state["allowed_labels"]]
        if result.area not in state["areas"]:
            data["area"] = "other"
        return {
            "classification": data,
            "_summary": f"{result.type}/{data['area']}/{result.severity}",
        }

    @timed("retrieve", "RETRIEVER")
    async def retrieve_node(state: TriageState) -> dict[str, Any]:
        query = f"{state['title']}\n\n{state.get('sanitized', '')[:1500]}"
        variant: Variant = state.get("retrieval_variant", "hybrid_rerank")  # type: ignore[assignment]
        created = datetime.fromisoformat(state["created_at"])
        own_key = doc_key_issue(state["repo"], state["number"])
        issues = await deps.search.search(
            query,
            variant=variant,
            k=MAX_EVIDENCE,
            filters=SearchFilters(
                repos=[state["repo"]],
                kinds=ISSUE_KINDS,
                created_before=created,
                exclude_doc_keys=[own_key],
            ),
        )
        docs = await deps.search.search(
            query, variant=variant, k=3, filters=SearchFilters(repos=[state["repo"]], kinds=["doc"])
        )
        merged = sorted([*issues, *docs], key=lambda h: h.score, reverse=True)[:MAX_EVIDENCE]
        evidence = [
            {
                "id": f"S{i + 1}",
                "doc_key": hit.doc_key,
                "kind": hit.kind,
                "title": hit.title,
                "url": hit.url,
                "number": hit.number,
                "score": round(hit.score, 4),
                "text": hit.text[:EVIDENCE_CHARS],
            }
            for i, hit in enumerate(merged)
        ]
        candidates = [
            {
                "doc_key": h.doc_key,
                "number": h.number,
                "title": h.title,
                "url": h.url,
                "score": round(h.score, 4),
                "text": h.text[:EVIDENCE_CHARS],
            }
            for h in issues
            if h.kind in ("issue", "discussion")
        ][:DUPLICATE_CANDIDATES]
        return {
            "evidence": evidence,
            "duplicate_candidates": candidates,
            "_summary": f"{len(issues)} threads, {len(docs)} docs",
        }

    @timed("dedupe", "LLM")
    async def dedupe_node(state: TriageState) -> dict[str, Any]:
        system = load_prompt(state["prompt_version"], "dedupe", repo=state["repo"])
        results: list[dict[str, Any]] = []
        new_block = wrap_untrusted(
            "new_issue", f"{state['title']}\n\n{state.get('sanitized', '')[:2000]}"
        )
        for cand in state.get("duplicate_candidates", []):
            try:
                verdict = await deps.llm.generate(
                    "dedupe",
                    [
                        Message.of("system", system),
                        Message.of(
                            "user",
                            f"{new_block}\n\n"
                            + wrap_untrusted(
                                "existing_issue", f"{cand['title']}\n\n{cand['text']}"
                            ),
                        ),
                    ],
                    DuplicateVerdict,
                )
            except StructuredOutputError as exc:
                return {"duplicates": results, "errors": [str(exc)], "_summary": "partial"}
            results.append(
                {**{k: v for k, v in cand.items() if k != "text"}, **verdict.model_dump()}
            )
        dupes = [
            r for r in results if r["is_duplicate"] and r["confidence"] >= DUPLICATE_MIN_CONFIDENCE
        ]
        return {
            "duplicates": results,
            "_summary": f"{len(dupes)} likely duplicates of {len(results)}",
        }

    @timed("draft", "LLM")
    async def draft_node(state: TriageState) -> dict[str, Any]:
        attempts = state.get("draft_attempts", 0) + 1
        evidence = state.get("evidence", [])
        if not evidence:
            refusal = Draft(
                claims=[],
                can_answer=False,
                refusal_reason="No related threads or docs were found.",
                reply_markdown=ASK_FOR_INFO,
                repro_checklist=["Minimal reproducible example", "Versions", "Full traceback"],
            )
            return {
                "draft": refusal.model_dump(),
                "draft_attempts": attempts,
                "_summary": "refused: no evidence",
            }
        system = load_prompt(state["prompt_version"], "draft", repo=state["repo"])
        items = "\n\n".join(
            f"[{e['id']}] {e['kind']} {e['url']}\n" + wrap_untrusted(e["id"], e["text"])
            for e in evidence
        )
        feedback = state.get("draft_feedback", [])
        user = f"{_issue_block(state)}\n\nEvidence:\n{items}"
        if feedback:
            user += "\n\nYour previous draft was rejected by the checker:\n- " + "\n- ".join(
                feedback
            )
        try:
            draft = await deps.llm.generate(
                "draft", [Message.of("system", system), Message.of("user", user)], Draft
            )
        except StructuredOutputError as exc:
            return {
                "draft": None,
                "draft_attempts": attempts,
                "needs_human": True,
                "errors": [str(exc)],
                "_summary": "failed",
            }
        data = draft.model_dump()
        data["reply_markdown"], redacted = redact(draft.reply_markdown)
        data["redactions"] = redacted
        mode = "answer" if draft.can_answer else "refusal"
        return {
            "draft": data,
            "draft_attempts": attempts,
            "_summary": f"{mode}, {len(draft.claims)} claims",
        }

    @timed("verify", "GUARDRAIL")
    async def verify_node(state: TriageState) -> dict[str, Any]:
        problems: list[str] = []
        warnings: list[str] = []
        if state.get("injection_flags"):
            warnings.append(f"possible prompt injection: {state['injection_flags']}")
        classification = state.get("classification")
        if classification is not None:
            raw = Classification.model_validate(
                {
                    **{k: v for k, v in classification.items() if k in Classification.model_fields},
                    "labels": classification.get("proposed_labels_raw", classification["labels"]),
                }
            )
            warnings += verify_classification(raw, state["allowed_labels"], state["areas"])
        draft_data = state.get("draft")
        if draft_data is not None:
            draft = Draft.model_validate(
                {k: v for k, v in draft_data.items() if k in Draft.model_fields}
            )
            sources = {e["id"]: e["text"] for e in state.get("evidence", [])}
            problems += verify_draft(draft, sources)
            if not problems and deps.llm_critic and draft.claims:
                listing = "\n".join(
                    f"{i}. quote: {c.quote}\n   statement: {c.statement}"
                    for i, c in enumerate(draft.claims)
                )
                try:
                    verdict = await deps.llm.generate(
                        "critic",
                        [
                            Message.of("system", load_prompt(state["prompt_version"], "critic")),
                            Message.of("user", listing),
                        ],
                        CriticVerdict,
                    )
                    problems += [
                        f"claim {i} is not supported by its quote"
                        for i in verdict.unsupported_claims
                    ]
                except StructuredOutputError as exc:
                    warnings.append(str(exc))
        ok = not problems
        update: dict[str, Any] = {
            "verification": {"ok": ok, "problems": problems, "warnings": warnings},
            "draft_feedback": problems,
            "_summary": "ok" if ok else f"{len(problems)} problems",
        }
        if (
            not ok
            and state.get("draft_attempts", 0) >= MAX_DRAFT_ATTEMPTS
            and draft_data is not None
        ):
            # Out of retries: replace the answer with a safe refusal, keep the checklist.
            update["draft"] = {
                **Draft(
                    claims=[],
                    can_answer=False,
                    refusal_reason="Draft failed citation checks: " + "; ".join(problems),
                    reply_markdown=ASK_FOR_INFO,
                    repro_checklist=list(draft_data.get("repro_checklist", [])),
                ).model_dump(),
                "redactions": [],
            }
            update["needs_human"] = True
        return update

    def after_verify(state: TriageState) -> Literal["draft", "approval"]:
        verification = state.get("verification", {})
        if not verification.get("ok", True) and state.get("draft_attempts", 0) < MAX_DRAFT_ATTEMPTS:
            return "draft"
        return "approval"

    @timed("approval", "CHAIN")
    async def approval_node(state: TriageState) -> dict[str, Any]:
        decision = interrupt(
            {
                "run_id": state.get("run_id"),
                "repo": state["repo"],
                "number": state["number"],
                "needs_human": state.get("needs_human", False),
            }
        )
        if not isinstance(decision, dict) or decision.get("decision") not in (
            "approve",
            "edit",
            "reject",
        ):
            raise ValueError(f"invalid decision payload: {decision!r}")
        return {"decision": decision, "_summary": str(decision["decision"])}

    @timed("act", "TOOL")
    async def act_node(state: TriageState) -> dict[str, Any]:
        decision = state.get("decision") or {}
        if decision.get("decision") == "reject":
            return {"applied": None, "status": "rejected", "_summary": "rejected, nothing applied"}
        edited = decision.get("edited") or {}
        draft = state.get("draft") or {}
        classification = state.get("classification") or {}
        reply = str(edited.get("reply_markdown") or draft.get("reply_markdown") or "")
        labels_source = edited.get("labels") if "labels" in edited else None
        if labels_source is None:
            labels_source = classification.get("labels", [])
        labels = [label for label in labels_source if label in state["allowed_labels"]]
        if deps.actions is None:
            return {
                "applied": None,
                "status": "approved",
                "_summary": "approved (no sandbox configured)",
            }
        applied = await deps.actions.apply(
            upstream_repo=state["repo"],
            upstream_number=state["number"],
            upstream_title=state["title"],
            upstream_url=state.get("url", ""),
            reply_markdown=reply,
            labels=labels,
        )
        return {
            "applied": {
                "issue_url": applied.issue_url,
                "comment_url": applied.comment_url,
                "labels": applied.labels,
            },
            "status": "applied",
            "_summary": f"applied to {deps.actions.sandbox_repo}",
        }

    graph = StateGraph(TriageState)
    nodes: dict[str, Node] = {
        "sanitize": sanitize_node,
        "classify": classify_node,
        "retrieve": retrieve_node,
        "dedupe": dedupe_node,
        "draft": draft_node,
        "verify": verify_node,
        "approval": approval_node,
        "act": act_node,
    }
    for name, fn in nodes.items():
        graph.add_node(name, fn)  # type: ignore[call-overload]
    graph.add_edge(START, "sanitize")
    graph.add_edge("sanitize", "classify")
    graph.add_edge("classify", "retrieve")
    graph.add_edge("retrieve", "dedupe")
    graph.add_edge("dedupe", "draft")
    graph.add_edge("draft", "verify")
    graph.add_conditional_edges("verify", after_verify, ["draft", "approval"])
    graph.add_edge("approval", "act")
    graph.add_edge("act", END)
    return graph.compile(checkpointer=checkpointer)
