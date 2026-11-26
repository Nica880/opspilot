# ADR-006: Agent design: explicit graph, structured outputs, deterministic critic

- Status: accepted
- Date: 2026-11-26

## Context

The agent runs on 7-8B local models (qwen2.5:7b-instruct, llama3.1:8b). They follow schemas
reasonably well when decoding is constrained, but they hallucinate citations and are easy to
steer with instructions hidden in issue text. A maintainer must stay in control of every write.

## Decision

- **Explicit LangGraph state machine.** The nodes are `sanitize → classify → retrieve → dedupe →
  draft → verify → approval → act`, not a free-form tool-calling loop. Every step is inspectable,
  traceable (one OpenTelemetry span per node) and testable on its own.
- **Structured outputs.** Every LLM call passes the Pydantic JSON schema as Ollama's `format`
  (constrained decoding) at temperature 0. Fields are ordered evidence-first (`evidence_quotes`
  before `type`, `claims` before `reply_markdown`) and all keys are required: with
  values-before-quotes, 7-8B models emit the value and then rationalise it. Output is validated by
  Pydantic. On failure the model gets the validation errors back, at most 2 repairs. After that
  the node records the error, sets `needs_human`, and the run still reaches the approval queue.
- **Grounding contract.** The draft may only use the numbered evidence items. Each factual
  statement is a `claim` carrying a `source_id` and a verbatim quote. The verify node checks that:
  - each cited source exists;
  - each quote appears in its source (normalised match, or 90% of its tokens; no paraphrases);
  - inline `[S#]` references all have claims;
  - labels and area are in the allowed sets.
  An optional LLM critic checks entailment. A failing draft is retried once with the problems as
  feedback, then replaced by a safe refusal that asks for a reproduction.
- **Refuse when there is no evidence.** With no retrieved evidence the draft node does not call the
  model at all. It produces a refusal plus a reproduction checklist.
- **Untrusted input handling** (`agent/guardrails.py`):
  - unicode normalisation; HTML comments, tags and zero-width characters are stripped;
  - a regex scan for injection families (instruction override, role hijack, prompt markup,
    exfiltration, tool abuse, remote exec), applied to the hidden HTML-comment content too;
  - `<untrusted>` delimiters around all issue/evidence text, with look-alike delimiters removed;
  - flags are shown to the reviewer as warnings, not hidden.
- **Writes have exactly one path**: `SandboxActions`, which calls `check_action` before every
  request. The allow-list is `create_mirror_issue`, `comment` and `add_labels`, only on
  `SANDBOX_REPO`. Approved actions create a mirror issue in the sandbox (link only, no upstream
  text), comment the (redacted) reply and apply allowed labels. Nothing is ever posted upstream.
- **Labels are the maintainers' own vocabulary**, mined from the corpus (labels used ≥5 times on
  issues), so proposals can be scored against history (label F1 in milestone 5).
- **Prompts are versioned files** (`agent/prompts/v1`, `v2`); a version only overrides what it
  changes.
- **CI uses a deterministic fake provider** behind the same interface. It parses the same prompts
  and answers with keyword heuristics. CI tests therefore cover the graph, the guardrails and the
  checks, not model quality; model quality is measured by `make eval` on real models.

## Consequences

- Strict quote checking rejects some correct but loosely quoted answers; they become refusals that
  a human can edit. We prefer a refusal to an ungrounded answer.
- The regex scanner is a tripwire, not a defence on its own. The real guarantees come from
  structure: the model cannot call tools and every write is gated by the allow-list and a human.
