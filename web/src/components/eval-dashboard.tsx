"use client";

import { useQuery } from "@tanstack/react-query";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError, getJSON } from "@/lib/api";
import { formatDuration, formatPercent } from "@/lib/format";

interface TriageReport {
  file: string;
  meta: Record<string, string | number>;
  results: {
    config: { model: string; prompt_version: string; retrieval_variant: string };
    n: number;
    label_micro_f1: number;
    label_macro_f1: number;
    dup_recall_at_5: number;
    linked_recall_at_6: number;
    groundedness: number | null;
    citations_correct: number | null;
    refusal_rate: number;
    p50_ms: number;
    p95_ms: number;
    issues_per_hour: number;
  }[];
}

interface RetrievalReport {
  file: string;
  meta: Record<string, string>;
  results: {
    variant: string;
    n: number;
    recall: Record<string, number>;
    mrr: number;
    p95_ms: number;
  }[];
}

interface RedteamReport {
  file: string;
  meta: Record<string, string>;
  summary: { cases: number; passed: number; pass_rate: number };
  results: { id: string; passed: boolean; failures: string[] }[];
}

function useReport<T>(kind: string) {
  return useQuery({
    queryKey: ["evals", kind],
    queryFn: async () => {
      try {
        return await getJSON<T>(`/api/evals/${kind}/latest`);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
  });
}

function Empty({ command }: { command: string }) {
  return (
    <p className="text-sm text-muted-foreground">
      No report yet. Run <code>{command}</code>.
    </p>
  );
}

export function EvalDashboard() {
  const triage = useReport<TriageReport>("triage");
  const retrieval = useReport<RetrievalReport>("retrieval");
  const redteam = useReport<RedteamReport>("redteam");

  return (
    <section className="space-y-4">
      <h1 className="text-xl font-semibold">Evaluation</h1>
      <Card>
        <CardHeader>
          <CardTitle>Triage matrix {triage.data && <small>({triage.data.file})</small>}</CardTitle>
        </CardHeader>
        <CardContent>
          {triage.data ? (
            <Table data-testid="triage-table">
              <TableHeader>
                <TableRow>
                  <TableHead>Config</TableHead>
                  <TableHead>n</TableHead>
                  <TableHead>Label F1 (micro/macro)</TableHead>
                  <TableHead>Dup recall@5</TableHead>
                  <TableHead>Linked recall@6</TableHead>
                  <TableHead>Grounded</TableHead>
                  <TableHead>Citations ok</TableHead>
                  <TableHead>p50 / p95</TableHead>
                  <TableHead>Issues/h</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {triage.data.results.map((r) => (
                  <TableRow
                    key={`${r.config.model}-${r.config.prompt_version}-${r.config.retrieval_variant}`}
                  >
                    <TableCell>
                      {r.config.model} / {r.config.prompt_version} / {r.config.retrieval_variant}
                    </TableCell>
                    <TableCell>{r.n}</TableCell>
                    <TableCell>
                      {r.label_micro_f1.toFixed(3)} / {r.label_macro_f1.toFixed(3)}
                    </TableCell>
                    <TableCell>{formatPercent(r.dup_recall_at_5, 1)}</TableCell>
                    <TableCell>{formatPercent(r.linked_recall_at_6, 1)}</TableCell>
                    <TableCell>{r.groundedness?.toFixed(2) ?? "n/a"}</TableCell>
                    <TableCell>{formatPercent(r.citations_correct, 1)}</TableCell>
                    <TableCell>
                      {formatDuration(r.p50_ms)} / {formatDuration(r.p95_ms)}
                    </TableCell>
                    <TableCell>{Math.round(r.issues_per_hour)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <Empty command="make eval" />
          )}
        </CardContent>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Duplicate retrieval</CardTitle>
          </CardHeader>
          <CardContent>
            {retrieval.data ? (
              <Table data-testid="retrieval-table">
                <TableHeader>
                  <TableRow>
                    <TableHead>Variant</TableHead>
                    <TableHead>n</TableHead>
                    <TableHead>R@1</TableHead>
                    <TableHead>R@5</TableHead>
                    <TableHead>MRR</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {retrieval.data.results.map((r) => (
                    <TableRow key={r.variant}>
                      <TableCell>{r.variant}</TableCell>
                      <TableCell>{r.n}</TableCell>
                      <TableCell>{formatPercent(r.recall["1"], 1)}</TableCell>
                      <TableCell>{formatPercent(r.recall["5"], 1)}</TableCell>
                      <TableCell>{r.mrr.toFixed(3)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <Empty command="make eval-retrieval" />
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Red team</CardTitle>
          </CardHeader>
          <CardContent className="text-sm">
            {redteam.data ? (
              <div data-testid="redteam">
                <p className="text-2xl font-semibold">
                  {redteam.data.summary.passed}/{redteam.data.summary.cases} passed
                </p>
                <p className="text-muted-foreground">
                  {redteam.data.meta.llm_provider} · {redteam.data.meta.model} · {redteam.data.file}
                </p>
                {redteam.data.results
                  .filter((r) => !r.passed)
                  .map((r) => (
                    <p key={r.id} className="text-destructive">
                      {r.id}: {r.failures.join("; ")}
                    </p>
                  ))}
              </div>
            ) : (
              <Empty command="make redteam" />
            )}
          </CardContent>
        </Card>
      </div>
    </section>
  );
}
