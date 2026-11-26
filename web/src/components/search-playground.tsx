"use client";

import { useQueries } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { getJSON, VARIANTS, type SearchResponse } from "@/lib/api";

const LABEL: Record<string, string> = {
  dense: "Dense (nomic-embed-text)",
  hybrid: "Hybrid (dense + BM25, RRF)",
  hybrid_rerank: "Hybrid + rerank (bge-reranker-base)",
};

export function SearchPlayground() {
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const results = useQueries({
    queries: VARIANTS.map((variant) => ({
      queryKey: ["search", variant, query],
      queryFn: () =>
        getJSON<SearchResponse>(
          `/api/search?${new URLSearchParams({ q: query, variant, k: "8" }).toString()}`,
        ),
      enabled: query.length >= 2,
    })),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    setQuery(draft.trim());
  }

  return (
    <section className="space-y-4">
      <h1 className="text-xl font-semibold">Search playground</h1>
      <form onSubmit={submit} className="flex gap-2">
        <Input
          aria-label="Query"
          placeholder="e.g. dependency with yield raises RuntimeError at shutdown"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <Button type="submit">Search</Button>
      </form>
      <div className="grid gap-4 lg:grid-cols-3">
        {VARIANTS.map((variant, i) => (
          <Card key={variant} data-testid={`results-${variant}`}>
            <CardHeader>
              <CardTitle className="text-sm">{LABEL[variant]}</CardTitle>
            </CardHeader>
            <CardContent>
              {results[i].isFetching && <p className="text-xs text-muted-foreground">Searching…</p>}
              <ol className="space-y-2 text-sm">
                {(results[i].data?.hits ?? []).map((hit) => (
                  <li key={hit.doc_key}>
                    <a href={hit.url} target="_blank" rel="noreferrer" className="hover:underline">
                      {hit.title}
                    </a>
                    <div className="flex gap-2 text-xs text-muted-foreground">
                      <Badge variant="outline">{hit.kind}</Badge>
                      <span>{hit.doc_key}</span>
                      <span>{hit.score.toFixed(3)}</span>
                    </div>
                  </li>
                ))}
              </ol>
            </CardContent>
          </Card>
        ))}
      </div>
    </section>
  );
}
