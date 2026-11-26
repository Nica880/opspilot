export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type RunStatus =
  | "running"
  | "awaiting_approval"
  | "needs_human"
  | "approved"
  | "rejected"
  | "applied"
  | "failed"
  | "expired";

export interface Step {
  node: string;
  ms: number;
  summary: string;
}

export interface Classification {
  evidence_quotes: string[];
  reasoning: string;
  type: "bug" | "feature" | "question" | "docs";
  area: string;
  severity: "low" | "medium" | "high" | "critical";
  labels: string[];
  confidence: number;
}

export interface Evidence {
  id: string;
  doc_key: string;
  kind: string;
  title: string;
  url: string;
  number: number | null;
  score: number;
}

export interface Duplicate {
  doc_key: string;
  number: number | null;
  title: string;
  url: string;
  score: number;
  shared_symptoms: string;
  differences: string;
  is_duplicate: boolean;
  confidence: number;
}

export interface Claim {
  source_id: string;
  quote: string;
  statement: string;
}

export interface Draft {
  claims: Claim[];
  can_answer: boolean;
  refusal_reason: string;
  reply_markdown: string;
  repro_checklist: string[];
}

export interface Suggestions {
  classification: Classification | null;
  labels: string[];
  duplicates: Duplicate[];
  draft: Draft | null;
  repro_checklist: string[];
  verification: { ok?: boolean; problems?: string[]; warnings?: string[] };
  evidence: Evidence[];
  injection_flags: string[];
  needs_human: boolean;
  errors: string[];
}

export interface Run {
  run_id: string;
  repo: string;
  number: number;
  title: string;
  status: RunStatus;
  model: string;
  prompt_version: string;
  retrieval_variant: string;
  latency_ms: number | null;
  started_at: string;
  steps: Step[];
  suggestions: Partial<Suggestions>;
  decision: Record<string, unknown> | null;
  error: string | null;
}

export interface RunSummary {
  run_id: string;
  repo: string;
  number: number;
  title: string;
  status: RunStatus;
  steps: string[];
  type: string | null;
  severity: string | null;
  labels: string[];
  needs_human: boolean;
  latency_ms: number | null;
}

export interface InboxItem {
  repo: string;
  number: number;
  title: string;
  url: string;
  labels: string[];
  state: string;
  created_at: string;
  run: RunSummary | null;
}

export interface Comment {
  id: number;
  author: string | null;
  author_association: string | null;
  body: string;
  html_url: string;
  created_at: string;
}

export interface IssueThread {
  repo: string;
  issue: {
    number: number;
    title: string;
    body: string;
    state: string;
    author: string | null;
    labels: string[];
    html_url: string;
    created_at: string;
  };
  comments: Comment[];
  run: Run | null;
}

export type Variant = "dense" | "hybrid" | "hybrid_rerank";
export const VARIANTS: Variant[] = ["dense", "hybrid", "hybrid_rerank"];

export interface SearchHit {
  doc_key: string;
  score: number;
  kind: string;
  repo: string;
  title: string;
  url: string;
  snippet: string;
  number: number | null;
}

export interface SearchResponse {
  query: string;
  variant: Variant;
  hits: SearchHit[];
}

export interface Ops {
  runs_by_status: Record<string, number>;
  approval_rate: number | null;
  edit_rate: number | null;
  latency_ms: { p50: number | null; p95: number | null };
  consumer_lag: { group: string; topic: string | null; lag: number | null; error?: string }[];
  links: Record<string, string>;
}

export interface DecisionBody {
  decision: "approve" | "edit" | "reject";
  reviewer: string;
  edited?: { reply_markdown?: string; labels?: string[] };
  note?: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return (await res.json()) as T;
}

export async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return (await res.json()) as T;
}

export function issuePath(repo: string, number: number): string {
  return `/issues/${repo}/${number}`;
}

/** Turn the reviewer's edit form into a decision payload; labels are comma separated. */
export function buildDecision(
  decision: DecisionBody["decision"],
  reviewer: string,
  reply: string,
  labelsText: string,
  original: { reply: string; labels: string[] },
): DecisionBody {
  const labels = labelsText
    .split(",")
    .map((l) => l.trim())
    .filter(Boolean);
  if (decision !== "edit") return { decision, reviewer };
  const edited: DecisionBody["edited"] = {};
  if (reply !== original.reply) edited.reply_markdown = reply;
  if (labels.join(",") !== original.labels.join(",")) edited.labels = labels;
  return { decision, reviewer, edited };
}
