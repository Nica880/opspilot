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
import { getJSON, type Ops } from "@/lib/api";
import { formatDuration, formatPercent } from "@/lib/format";

export function OpsView() {
  const ops = useQuery({
    queryKey: ["ops"],
    queryFn: () => getJSON<Ops>("/api/ops"),
    refetchInterval: 5_000,
  });
  if (ops.isPending) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (ops.isError) return <p className="text-destructive">Could not load ops data.</p>;
  const data = ops.data;

  return (
    <section className="space-y-4">
      <h1 className="text-xl font-semibold">Operations</h1>
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Workflow states</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1 text-sm" data-testid="states">
              {Object.entries(data.runs_by_status).map(([status, n]) => (
                <li key={status} className="flex justify-between">
                  <span>{status}</span>
                  <span className="font-mono">{n}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Review</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <p>Approval rate: {formatPercent(data.approval_rate, 1)}</p>
            <p>Edit rate: {formatPercent(data.edit_rate, 1)}</p>
            <p>
              Latency p50 / p95: {formatDuration(data.latency_ms.p50)} /{" "}
              {formatDuration(data.latency_ms.p95)}
            </p>
            <p>Cost: $0 (local models)</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Links</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1 text-sm">
              {Object.entries(data.links).map(([name, url]) => (
                <li key={name}>
                  <a href={url} target="_blank" rel="noreferrer" className="underline">
                    {name.replace("_", " ")}
                  </a>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Redpanda consumer lag</CardTitle>
        </CardHeader>
        <CardContent>
          <Table data-testid="lag">
            <TableHeader>
              <TableRow>
                <TableHead>Group</TableHead>
                <TableHead>Topic</TableHead>
                <TableHead>Lag</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.consumer_lag.map((row) => (
                <TableRow key={`${row.group}-${row.topic}`}>
                  <TableCell>{row.group}</TableCell>
                  <TableCell>{row.topic ?? "-"}</TableCell>
                  <TableCell>{row.lag ?? row.error ?? "n/a"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </section>
  );
}
