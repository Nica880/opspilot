import { Suspense } from "react";

import { IssueView } from "@/components/issue-view";

export default function IssuePage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <IssueView />
    </Suspense>
  );
}
