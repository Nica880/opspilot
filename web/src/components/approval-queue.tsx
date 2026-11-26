"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState, useSyncExternalStore } from "react";

import { ClassificationCard, CitedText, StatusBadge } from "@/components/run-panels";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  buildDecision,
  getJSON,
  issuePath,
  postJSON,
  type DecisionBody,
  type Run,
} from "@/lib/api";
import { useRunStream } from "@/lib/sse";

const REVIEWER_KEY = "opspilot.reviewer";

function subscribeStorage(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}

function ApprovalCard({ run, reviewer }: { run: Run; reviewer: string }) {
  const queryClient = useQueryClient();
  const s = run.suggestions;
  const originalReply = s.draft?.reply_markdown ?? "";
  const originalLabels = s.labels ?? [];
  const [editing, setEditing] = useState(false);
  const [reply, setReply] = useState(originalReply);
  const [labels, setLabels] = useState(originalLabels.join(", "));
  const decide = useMutation({
    mutationFn: (decision: DecisionBody["decision"]) =>
      postJSON(
        `/api/runs/${run.run_id}/decision`,
        buildDecision(decision, reviewer, reply, labels, {
          reply: originalReply,
          labels: originalLabels,
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["runs"] }),
  });

  return (
    <Card data-testid="approval-card">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          <Link href={issuePath(run.repo, run.number)} className="hover:underline">
            {run.repo}#{run.number}: {run.title}
          </Link>
          <StatusBadge status={run.status} />
          {s.needs_human && <Badge variant="destructive">needs human</Badge>}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <ClassificationCard s={s} />
        {editing ? (
          <div className="space-y-2">
            <Textarea
              aria-label="Reply"
              value={reply}
              onChange={(e) => setReply(e.target.value)}
              rows={6}
            />
            <Input aria-label="Labels" value={labels} onChange={(e) => setLabels(e.target.value)} />
          </div>
        ) : (
          <CitedText text={originalReply} />
        )}
        <div className="flex gap-2">
          {editing ? (
            <Button onClick={() => decide.mutate("edit")} disabled={!reviewer || decide.isPending}>
              Save edit &amp; apply to sandbox
            </Button>
          ) : (
            <Button
              onClick={() => decide.mutate("approve")}
              disabled={!reviewer || decide.isPending}
            >
              Approve
            </Button>
          )}
          <Button variant="outline" onClick={() => setEditing((v) => !v)}>
            {editing ? "Cancel edit" : "Edit"}
          </Button>
          <Button
            variant="destructive"
            onClick={() => decide.mutate("reject")}
            disabled={!reviewer || decide.isPending}
          >
            Reject
          </Button>
        </div>
        {decide.isSuccess && <p data-testid="decision-sent">Decision sent.</p>}
        {decide.isError && <p className="text-destructive">{String(decide.error)}</p>}
      </CardContent>
    </Card>
  );
}

export function ApprovalQueue() {
  useRunStream();
  // The page is prerendered: read localStorage via an external-store subscription (server: "").
  const stored = useSyncExternalStore(
    subscribeStorage,
    () => localStorage.getItem(REVIEWER_KEY) ?? "",
    () => "",
  );
  const [typed, setTyped] = useState<string | null>(null);
  const reviewer = typed ?? stored;
  const awaiting = useQuery({
    queryKey: ["runs", "awaiting_approval"],
    queryFn: () => getJSON<Run[]>("/api/runs?status=awaiting_approval"),
  });
  const needsHuman = useQuery({
    queryKey: ["runs", "needs_human"],
    queryFn: () => getJSON<Run[]>("/api/runs?status=needs_human"),
  });
  const runs = [...(needsHuman.data ?? []), ...(awaiting.data ?? [])];

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-xl font-semibold">Approval queue</h1>
        <Input
          aria-label="Reviewer"
          placeholder="Your name (recorded with each decision)"
          className="max-w-xs"
          value={reviewer}
          onChange={(e) => {
            setTyped(e.target.value);
            localStorage.setItem(REVIEWER_KEY, e.target.value);
          }}
        />
      </div>
      <p className="text-sm text-muted-foreground">
        Approved actions are applied to the sandbox repo only. Edits become ground truth.
      </p>
      {runs.length === 0 && <p className="text-sm text-muted-foreground">Nothing to review.</p>}
      {runs.map((run) => (
        <ApprovalCard key={run.run_id} run={run} reviewer={reviewer} />
      ))}
    </section>
  );
}
