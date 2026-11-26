"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";

import {
  ClassificationCard,
  DraftCard,
  DuplicatesPanel,
  EvidencePanel,
  StatusBadge,
  StepsTimeline,
} from "@/components/run-panels";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getJSON, type IssueThread } from "@/lib/api";
import { useRunStream } from "@/lib/sse";

export function IssueView() {
  const params = useParams<{ owner: string; name: string; number: string }>();
  const repo = `${params.owner}/${params.name}`;
  const number = Number(params.number);
  useRunStream();
  const thread = useQuery({
    queryKey: ["issue", repo, number],
    queryFn: () => getJSON<IssueThread>(`/api/issues/${repo}/${number}`),
  });

  if (thread.isPending) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (thread.isError) return <p className="text-destructive">Issue not found.</p>;
  const { issue, comments, run } = thread.data;
  const s = run?.suggestions ?? {};

  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">{issue.title}</h1>
        <p className="text-sm text-muted-foreground">
          <a href={issue.html_url} target="_blank" rel="noreferrer" className="underline">
            {repo}#{issue.number}
          </a>{" "}
          · {issue.state} · opened by {issue.author ?? "unknown"}
        </p>
        {run && (
          <div className="mt-2 flex items-center gap-3">
            <StatusBadge status={run.status} />
            <StepsTimeline run={run} />
          </div>
        )}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Thread</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <p className="whitespace-pre-wrap">{issue.body}</p>
              {comments.map((c) => (
                <div key={c.id} className="border-t pt-2">
                  <p className="text-xs text-muted-foreground">
                    {c.author} {c.author_association !== "NONE" && `(${c.author_association})`}
                  </p>
                  <p className="whitespace-pre-wrap">{c.body}</p>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
        <div className="space-y-4">
          {run ? (
            <>
              <ClassificationCard s={s} />
              <DraftCard s={s} />
              <EvidencePanel evidence={s.evidence ?? []} />
            </>
          ) : (
            <p className="text-sm text-muted-foreground">Not triaged yet.</p>
          )}
        </div>
      </div>
      {run && <DuplicatesPanel duplicates={s.duplicates ?? []} current={{ title: issue.title }} />}
    </section>
  );
}
