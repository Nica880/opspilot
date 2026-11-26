# ADR-007: Temporal around LangGraph for durable human approval

- Status: accepted
- Date: 2026-11-26

## Context

An approval can take minutes or days. Worker restarts, laptop sleep and Ollama hiccups must not
lose a run or apply an action twice.

## Decision

- **One Temporal workflow per issue**, id `triage-<owner>-<repo>-<number>` with
  `REJECT_DUPLICATE` reuse. The run id is `uuid5(workflow id)`. Starting twice is a no-op, so the
  Redpanda trigger can be at-least-once.
- **Workflow steps:**
  1. Activity `run_triage` runs the LangGraph graph until its `interrupt()` at the approval node.
     The graph is checkpointed in Postgres (`AsyncPostgresSaver`, thread id = run id).
  2. The workflow waits durably for the `decide` signal, up to 7 days, and then expires.
  3. Activity `resume_triage` resumes the graph with `Command(resume=decision)`, which runs `act`.
- **Signals are validated in the workflow**: only `approve | edit | reject`; the first valid
  decision wins and later ones are ignored.
- **Retries**: triage activities retry 3 times with backoff. LangGraph checkpoints make a retried
  activity continue from the last completed node instead of re-running finished LLM calls.
- **Concurrency**: the worker runs at most 2 activities at once; one 7B model on a laptop is the
  bottleneck.
- **Triggering**: a Redpanda consumer (`opspilot-triage` group) starts workflows only for
  `issue.opened` events from incremental sync, never for backfill or PRs. `make demo` and
  `POST /api/triage` start runs explicitly.

## Consequences

- Two persistence layers (Temporal history + LangGraph checkpoints), both on the same Postgres
  server. Temporal owns "what step of the business process", LangGraph owns "what step of the
  agent".
- The UI never talks to Temporal directly: `POST /api/runs/{id}/decision` validates the run state
  and signals the workflow.
