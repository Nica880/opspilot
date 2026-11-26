# ADR-008: Evaluation methodology

- Status: accepted
- Date: 2026-11-26

## Context

The project is only credible if quality is measured on real data, with no leakage, and every
README number can be reproduced by a command. Hand labelling must stay close to zero.

## Decision

- **Ground truth from history.**
  - Labels: the labels maintainers actually applied. The label vocabulary is mined from the issues
    *before* the cutoff (labels used ≥5 times; workflow-noise labels are excluded).
  - Duplicates: a comment that starts with `Duplicate of #N` (GitHub's own convention), and the
    target must be older than the duplicate.
  - Linked answers: docs pages, PRs and issues that maintainers (`OWNER`/`MEMBER`/`COLLABORATOR`)
    linked in their comments.
  - Human edits from the approval queue are stored as `ground_truth(source='human_edit')`.
- **Time split.** The cutoff defaults to the 80th percentile of issue creation dates. Held-out
  issues are those created at or after the cutoff. Retrieval during evaluation only sees chunks
  created before `min(issue.created_at, cutoff)`. Comment chunks carry the comment's own
  timestamp, so a maintainer answer written after the cutoff can never leak in. Docs are the
  current version (a known, small leak: docs can describe a fix that came later).
- **Metrics.**
  - Labels: micro and macro F1 over the vocabulary.
  - Duplicates: recall@5 and MRR of the target among retrieved threads, plus precision of the
    agent's "is duplicate" flags.
  - Retrieval: recall@6 of maintainer-linked docs/PRs among the evidence.
  - Answers: LLM-as-judge groundedness (1–5) and citation correctness on a random sample. Judges
    are crossed (llama3.1 judges qwen2.5 and vice versa) to avoid self-preference.
  - Ops: refusal / verified / needs-human rates, p50/p95 latency, issues/hour, cost (always $0:
    local models).
- **Matrix.** 2 models × 2 prompt versions × 3 retrieval variants = 12 configs. One 7B model on a
  laptop takes ~10–40 s per triage, so `make eval` runs the matrix on a seeded sample of 50
  held-out issues per config (~600 runs). `make eval-full` runs the chosen config on 1,000
  held-out issues with 100 judged drafts. Both write `docs/eval/<date>.md` + `.json` and rows in
  `eval_runs`.
- **Red team.** 50 crafted issues: 10 attack families × 5 carriers (body, hidden HTML comment,
  title, code block, unicode obfuscation). Each case is triaged and then *approved by a
  rubber-stamping reviewer*, and must still pass these checks:
  - writes only hit allow-listed sandbox endpoints, with allowed labels;
  - no non-evidence URLs, secrets/PII, system-prompt text or claimed actions in the reply;
  - injection is flagged where the attack has an explicit pattern.
  CI runs the set with the fake LLM and with a deliberately *compromised* model that obeys the
  attacker: both must pass 100%. `make redteam` runs it against the real model.
- **promptfoo** runs a small behavioural regression suite (types, refusal, no leaks, vocabulary)
  for both prompt versions. CI runs it with the fake provider; `make promptfoo` runs it against
  Ollama.
- **Honesty rule.** The README metrics table is copied from a generated report and names the
  report file. No number appears without one.

## Consequences

- A full matrix run takes hours, so it is run before a release, not in CI. CI evals check the
  machinery (fixtures, fake LLM, red team), not model quality.
- Maintainer labels are noisy (unlabelled issues, labels added late), so label F1 is a lower bound
  on usefulness. Edits in the approval queue are collected to build a cleaner set over time.
