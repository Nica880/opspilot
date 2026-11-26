"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { API_URL, type RunSummary } from "@/lib/api";

/** Live run updates over SSE; invalidates cached queries so pages refetch details. */
export function useRunStream(): { connected: boolean; last: RunSummary | null } {
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [last, setLast] = useState<RunSummary | null>(null);

  useEffect(() => {
    const source = new EventSource(`${API_URL}/api/stream/runs`);
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.addEventListener("run", (event) => {
      const run = JSON.parse((event as MessageEvent<string>).data) as RunSummary;
      setLast(run);
      void queryClient.invalidateQueries({ queryKey: ["inbox"] });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["issue", run.repo, run.number] });
    });
    return () => source.close();
  }, [queryClient]);

  return { connected, last };
}
