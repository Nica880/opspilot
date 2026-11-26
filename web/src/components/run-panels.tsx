import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Duplicate, Evidence, Run, RunStatus, Suggestions } from "@/lib/api";
import { splitCitations } from "@/lib/citations";
import { formatDuration, formatPercent } from "@/lib/format";

const STATUS_VARIANT: Record<RunStatus, "default" | "secondary" | "destructive" | "outline"> = {
  running: "secondary",
  awaiting_approval: "default",
  needs_human: "destructive",
  approved: "outline",
  applied: "outline",
  rejected: "outline",
  failed: "destructive",
  expired: "outline",
};

export function StatusBadge({ status }: { status: RunStatus }) {
  return (
    <Badge variant={STATUS_VARIANT[status]} data-testid="status">
      {status.replace("_", " ")}
    </Badge>
  );
}

export function ClassificationCard({ s }: { s: Partial<Suggestions> }) {
  const c = s.classification;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Classification</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {c ? (
          <>
            <div className="flex flex-wrap gap-2">
              <Badge data-testid="type">{c.type}</Badge>
              <Badge variant="secondary">area: {c.area}</Badge>
              <Badge
                variant={
                  c.severity === "critical" || c.severity === "high" ? "destructive" : "outline"
                }
              >
                {c.severity}
              </Badge>
              <span className="text-muted-foreground">
                confidence {formatPercent(c.confidence)}
              </span>
            </div>
            <p>{c.reasoning}</p>
            {c.evidence_quotes.map((q) => (
              <blockquote key={q} className="border-l-2 pl-3 text-muted-foreground italic">
                {q}
              </blockquote>
            ))}
            <div className="flex flex-wrap gap-1" data-testid="labels">
              {(s.labels ?? []).map((l) => (
                <Badge key={l} variant="outline">
                  {l}
                </Badge>
              ))}
            </div>
          </>
        ) : (
          <p className="text-destructive">Classification failed: sent to a human.</p>
        )}
        {(s.injection_flags ?? []).length > 0 && (
          <p className="text-destructive" data-testid="injection">
            Possible prompt injection: {(s.injection_flags ?? []).join(", ")}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

export function EvidencePanel({ evidence }: { evidence: Evidence[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Evidence</CardTitle>
      </CardHeader>
      <CardContent>
        {evidence.length === 0 ? (
          <p className="text-sm text-muted-foreground">No related threads or docs found.</p>
        ) : (
          <ol className="space-y-2 text-sm" data-testid="evidence">
            {evidence.map((e) => (
              <li key={e.id} id={`evidence-${e.id}`} className="flex gap-2">
                <Badge variant="secondary">{e.id}</Badge>
                <a href={e.url} target="_blank" rel="noreferrer" className="underline">
                  {e.title || e.doc_key}
                </a>
                <span className="text-muted-foreground">
                  {e.kind} · {e.score.toFixed(3)}
                </span>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

export function DuplicatesPanel({
  duplicates,
  current,
}: {
  duplicates: Duplicate[];
  current: { title: string; body?: string };
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Possible duplicates</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {duplicates.length === 0 && <p className="text-muted-foreground">No candidates.</p>}
        {duplicates.map((d) => (
          <div key={d.doc_key} className="grid gap-3 md:grid-cols-2" data-testid="duplicate">
            <div className="rounded-md border p-3">
              <p className="font-medium">This issue</p>
              <p>{current.title}</p>
            </div>
            <div className="rounded-md border p-3">
              <p className="font-medium">
                <a href={d.url} target="_blank" rel="noreferrer" className="underline">
                  #{d.number} {d.title}
                </a>{" "}
                {d.is_duplicate ? (
                  <Badge variant="destructive">duplicate {formatPercent(d.confidence)}</Badge>
                ) : (
                  <Badge variant="outline">related</Badge>
                )}
              </p>
              <p className="text-muted-foreground">Shared: {d.shared_symptoms}</p>
              <p className="text-muted-foreground">Differs: {d.differences}</p>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

export function CitedText({ text }: { text: string }) {
  return (
    <p className="whitespace-pre-wrap" data-testid="reply">
      {splitCitations(text).map((seg, i) =>
        "cite" in seg ? (
          <a key={i} href={`#evidence-${seg.cite}`} className="font-mono text-xs text-blue-600">
            [{seg.cite}]
          </a>
        ) : (
          <span key={i}>{seg.text}</span>
        ),
      )}
    </p>
  );
}

export function DraftCard({ s }: { s: Partial<Suggestions> }) {
  const d = s.draft;
  const v = s.verification ?? {};
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          Draft reply{" "}
          {d && !d.can_answer && <Badge variant="secondary">refusal: not enough evidence</Badge>}{" "}
          <Badge variant={v.ok ? "outline" : "destructive"} data-testid="verified">
            {v.ok ? "citations verified" : "citations failed checks"}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {d ? <CitedText text={d.reply_markdown} /> : <p>No draft.</p>}
        {d && !d.can_answer && d.refusal_reason && (
          <p className="text-muted-foreground">Why: {d.refusal_reason}</p>
        )}
        {(d?.claims ?? []).length > 0 && (
          <ul className="space-y-1 text-muted-foreground">
            {d!.claims.map((c, i) => (
              <li key={i}>
                <span className="font-mono">[{c.source_id}]</span> “{c.quote}”
              </li>
            ))}
          </ul>
        )}
        {(s.repro_checklist ?? []).length > 0 && (
          <div>
            <p className="font-medium">Reproduction checklist</p>
            <ul className="list-disc pl-5">
              {(s.repro_checklist ?? []).map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        )}
        {[...(v.problems ?? []), ...(v.warnings ?? [])].map((p) => (
          <p key={p} className="text-destructive">
            {p}
          </p>
        ))}
      </CardContent>
    </Card>
  );
}

export function StepsTimeline({ run }: { run: Run }) {
  return (
    <ol className="flex flex-wrap gap-2 text-xs" data-testid="steps">
      {run.steps.map((step, i) => (
        <li key={`${step.node}-${i}`} className="rounded border px-2 py-1" title={step.summary}>
          {step.node} · {formatDuration(step.ms)}
        </li>
      ))}
    </ol>
  );
}
