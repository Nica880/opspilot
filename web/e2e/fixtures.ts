import type { Page, Route } from "@playwright/test";

import type { InboxItem, IssueThread, Ops, Run, SearchResponse } from "../src/lib/api";

export const API = "http://localhost:8000";

export const RUN: Run = {
  run_id: "run-1",
  repo: "fastapi/fastapi",
  number: 101,
  title: "Dependency with yield raises RuntimeError on shutdown",
  status: "awaiting_approval",
  model: "qwen2.5:7b-instruct",
  prompt_version: "v2",
  retrieval_variant: "hybrid_rerank",
  latency_ms: 18_400,
  started_at: "2026-11-20T10:00:00+00:00",
  steps: [
    { node: "sanitize", ms: 2, summary: "clean" },
    { node: "classify", ms: 4100, summary: "bug/dependencies/high" },
    { node: "retrieve", ms: 900, summary: "6 threads, 3 docs" },
    { node: "dedupe", ms: 5200, summary: "1 likely duplicates of 3" },
    { node: "draft", ms: 7600, summary: "answer, 1 claims" },
    { node: "verify", ms: 3, summary: "ok" },
  ],
  suggestions: {
    classification: {
      evidence_quotes: ["the app crashes with RuntimeError when it shuts down"],
      reasoning: "A crash with a traceback: a bug in dependency teardown.",
      type: "bug",
      area: "dependencies",
      severity: "high",
      labels: ["bug"],
      confidence: 0.82,
    },
    labels: ["bug"],
    duplicates: [
      {
        doc_key: "fastapi/fastapi#42",
        number: 42,
        title: "RuntimeError at shutdown with yield dependencies",
        url: "https://github.com/fastapi/fastapi/issues/42",
        score: 0.91,
        shared_symptoms: "RuntimeError raised during shutdown from a yield dependency",
        differences: "none",
        is_duplicate: true,
        confidence: 0.8,
      },
    ],
    draft: {
      claims: [
        {
          source_id: "S1",
          quote: "The exit code in dependencies with yield runs after the response is sent.",
          statement: "Exit code runs after the response.",
        },
      ],
      can_answer: true,
      refusal_reason: "",
      reply_markdown:
        "Thanks! The exit code in dependencies with yield runs after the response is sent [S1].",
      repro_checklist: ["Library, Python and OS versions"],
    },
    repro_checklist: ["Library, Python and OS versions"],
    verification: { ok: true, problems: [], warnings: [] },
    evidence: [
      {
        id: "S1",
        doc_key: "fastapi/fastapi:docs/en/docs/tutorial/dependencies/dependencies-with-yield.md",
        kind: "doc",
        title: "Dependencies with yield",
        url: "https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/",
        number: null,
        score: 0.88,
      },
    ],
    injection_flags: [],
    needs_human: false,
    errors: [],
  },
  decision: null,
  error: null,
};

export const INBOX: InboxItem[] = [
  {
    repo: RUN.repo,
    number: RUN.number,
    title: RUN.title,
    url: "https://github.com/fastapi/fastapi/issues/101",
    labels: [],
    state: "open",
    created_at: "2026-11-20T09:59:00+00:00",
    run: {
      run_id: RUN.run_id,
      repo: RUN.repo,
      number: RUN.number,
      title: RUN.title,
      status: "awaiting_approval",
      steps: RUN.steps.map((s) => s.node),
      type: "bug",
      severity: "high",
      labels: ["bug"],
      needs_human: false,
      latency_ms: RUN.latency_ms,
    },
  },
  {
    repo: "astral-sh/uv",
    number: 7,
    title: "uv lock is slow on large workspaces",
    url: "https://github.com/astral-sh/uv/issues/7",
    labels: [],
    state: "open",
    created_at: "2026-11-19T09:00:00+00:00",
    run: null,
  },
];

export const THREAD: IssueThread = {
  repo: RUN.repo,
  issue: {
    number: RUN.number,
    title: RUN.title,
    body: "When my dependency uses yield, the app crashes with RuntimeError when it shuts down.",
    state: "open",
    author: "someone",
    labels: [],
    html_url: "https://github.com/fastapi/fastapi/issues/101",
    created_at: "2026-11-20T09:59:00+00:00",
  },
  comments: [
    {
      id: 1,
      author: "maintainer",
      author_association: "MEMBER",
      body: "Thanks, can you share versions?",
      html_url: "https://github.com/fastapi/fastapi/issues/101#issuecomment-1",
      created_at: "2026-11-20T11:00:00+00:00",
    },
  ],
  run: RUN,
};

export function searchResponse(variant: string): SearchResponse {
  return {
    query: "q",
    variant: variant as SearchResponse["variant"],
    hits: [
      {
        doc_key: `fastapi/fastapi#${variant.length}`,
        score: 0.5,
        kind: "issue",
        repo: "fastapi/fastapi",
        title: `${variant} top hit`,
        url: "https://github.com/fastapi/fastapi/issues/1",
        snippet: "",
        number: variant.length,
      },
    ],
  };
}

export const OPS: Ops = {
  runs_by_status: { awaiting_approval: 3, applied: 5 },
  approval_rate: 0.8,
  edit_rate: 0.2,
  latency_ms: { p50: 15_000, p95: 41_000 },
  consumer_lag: [
    { group: "opspilot-indexer", topic: "issues.v1", lag: 0 },
    { group: "opspilot-triage", topic: "issues.v1", lag: 4 },
  ],
  links: { temporal: "http://localhost:8233", phoenix: "http://localhost:6006" },
};

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "*",
  "access-control-allow-methods": "GET, POST",
};

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    headers: CORS,
    body: JSON.stringify(body),
  });
}

/** Mock every API endpoint the UI uses; returns the decision payloads the UI posted. */
export async function mockApi(page: Page): Promise<{ decisions: unknown[] }> {
  const decisions: unknown[] = [];
  await page.route(`${API}/api/**`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers: CORS });
    if (path === "/api/stream/runs") {
      return route.fulfill({
        status: 200,
        headers: { ...CORS, "content-type": "text/event-stream" },
        body: `event: run\ndata: ${JSON.stringify(INBOX[0].run)}\n\n`,
      });
    }
    if (path === "/api/inbox") return json(route, INBOX);
    if (path === `/api/issues/${RUN.repo}/${RUN.number}`) return json(route, THREAD);
    if (path === "/api/runs") {
      return json(route, url.searchParams.get("status") === "awaiting_approval" ? [RUN] : []);
    }
    if (path === `/api/runs/${RUN.run_id}/decision`) {
      decisions.push(request.postDataJSON());
      return json(route, { status: "accepted" }, 202);
    }
    if (path === "/api/search") {
      return json(route, searchResponse(url.searchParams.get("variant") ?? ""));
    }
    if (path === "/api/evals/redteam/latest") {
      return json(route, {
        file: "2026-11-26-redteam.json",
        meta: { llm_provider: "fake", model: "fake" },
        summary: { cases: 50, passed: 50, pass_rate: 1 },
        results: [],
      });
    }
    if (path.startsWith("/api/evals/")) return json(route, { detail: "no report" }, 404);
    if (path === "/api/ops") return json(route, OPS);
    return json(route, { detail: `unmocked ${path}` }, 404);
  });
  return { decisions };
}
