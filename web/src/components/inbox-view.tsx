"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { StatusBadge } from "@/components/run-panels";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { getJSON, issuePath, type InboxItem } from "@/lib/api";
import { useRunStream } from "@/lib/sse";

export function InboxView() {
  const { connected } = useRunStream();
  const inbox = useQuery({
    queryKey: ["inbox"],
    queryFn: () => getJSON<InboxItem[]>("/api/inbox"),
  });

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Inbox</h1>
        <span className="text-xs text-muted-foreground" data-testid="live">
          {connected ? "● live" : "○ disconnected"}
        </span>
      </div>
      {inbox.isError && <p className="text-destructive">Could not load the inbox.</p>}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Issue</TableHead>
            <TableHead>Triage</TableHead>
            <TableHead>Suggested</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {(inbox.data ?? []).map((item) => (
            <TableRow key={`${item.repo}#${item.number}`} data-testid="inbox-row">
              <TableCell className="max-w-md whitespace-normal">
                <Link
                  href={issuePath(item.repo, item.number)}
                  className="font-medium hover:underline"
                >
                  {item.title}
                </Link>
                <div className="text-xs text-muted-foreground">
                  {item.repo}#{item.number} · {new Date(item.created_at).toLocaleDateString()}
                </div>
              </TableCell>
              <TableCell>
                {item.run ? (
                  <div className="space-y-1">
                    <StatusBadge status={item.run.status} />
                    <div className="text-xs text-muted-foreground">
                      {item.run.steps.join(" → ")}
                    </div>
                  </div>
                ) : (
                  <span className="text-xs text-muted-foreground">not triaged</span>
                )}
              </TableCell>
              <TableCell className="space-x-1">
                {item.run?.type && <Badge>{item.run.type}</Badge>}
                {item.run?.severity && <Badge variant="secondary">{item.run.severity}</Badge>}
                {item.run?.labels.map((l) => (
                  <Badge key={l} variant="outline">
                    {l}
                  </Badge>
                ))}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {inbox.data?.length === 0 && (
        <p className="text-sm text-muted-foreground">No issues yet. Run `make demo`.</p>
      )}
    </section>
  );
}
