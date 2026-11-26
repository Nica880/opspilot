import { expect, test } from "@playwright/test";

import { mockApi, RUN } from "./fixtures";

test("inbox lists issues with live triage status", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  const rows = page.getByTestId("inbox-row");
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toContainText(RUN.title);
  await expect(rows.first().getByTestId("status")).toHaveText("awaiting approval");
  await expect(rows.nth(1)).toContainText("not triaged");
  await expect(page.getByTestId("live")).toHaveText("● live");
});

test("issue view shows the thread, evidence, verified draft and duplicates", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/issues/${RUN.repo}/${RUN.number}`);
  await expect(page.getByRole("heading", { name: RUN.title })).toBeVisible();
  await expect(page.getByText("Thanks, can you share versions?")).toBeVisible();
  await expect(page.getByTestId("type")).toHaveText("bug");
  await expect(page.getByTestId("verified")).toHaveText("citations verified");
  const citation = page.getByTestId("reply").getByRole("link", { name: "[S1]" });
  await expect(citation).toHaveAttribute("href", "#evidence-S1");
  await expect(page.getByTestId("evidence")).toContainText("Dependencies with yield");
  await expect(page.getByTestId("duplicate")).toContainText("#42");
  await expect(page.getByTestId("steps")).toContainText("classify");
});

test("approval queue sends approve and edit decisions with the reviewer", async ({ page }) => {
  const { decisions } = await mockApi(page);
  await page.goto("/approvals");
  await page.getByLabel("Reviewer").fill("nicanor");
  const card = page.getByTestId("approval-card");
  await expect(card).toContainText(RUN.title);

  await card.getByRole("button", { name: "Approve" }).click();
  await expect(card.getByTestId("decision-sent")).toBeVisible();
  expect(decisions[0]).toEqual({ decision: "approve", reviewer: "nicanor" });

  await card.getByRole("button", { name: "Edit" }).click();
  await card.getByLabel("Labels").fill("bug, investigate");
  await card.getByRole("button", { name: /Save edit/ }).click();
  await expect.poll(() => decisions.length).toBe(2);
  expect(decisions[1]).toEqual({
    decision: "edit",
    reviewer: "nicanor",
    edited: { labels: ["bug", "investigate"] },
  });
});

test("approval buttons stay disabled until a reviewer name is set", async ({ page }) => {
  await mockApi(page);
  await page.goto("/approvals");
  await page.getByLabel("Reviewer").fill("");
  await expect(page.getByRole("button", { name: "Approve" })).toBeDisabled();
});

test("the reviewer name is remembered across reloads", async ({ page }) => {
  await mockApi(page);
  await page.goto("/approvals");
  await page.getByLabel("Reviewer").fill("nicanor");
  await page.reload();
  await expect(page.getByLabel("Reviewer")).toHaveValue("nicanor");
  await expect(page.getByRole("button", { name: "Approve" })).toBeEnabled();
});

test("search playground compares the three retrieval variants", async ({ page }) => {
  await mockApi(page);
  await page.goto("/search");
  await page.getByLabel("Query").fill("yield RuntimeError");
  await page.getByRole("button", { name: "Search" }).click();
  const firstTitle = (variant: string) =>
    page.getByTestId(`results-${variant}`).getByRole("listitem").first();
  await expect(firstTitle("dense")).toContainText("Lifespan shutdown hangs");
  await expect(firstTitle("hybrid")).toContainText("RuntimeError at shutdown");
  await expect(firstTitle("hybrid_rerank")).toContainText("RuntimeError at shutdown");
  await expect(
    page.getByTestId("results-hybrid_rerank").getByRole("listitem").nth(1),
  ).toContainText("Dependencies with yield");
});

test("eval dashboard shows reports and empty states", async ({ page }) => {
  await mockApi(page);
  await page.goto("/evals");
  await expect(page.getByTestId("redteam")).toContainText("50/50 passed");
  await expect(page.getByText("make eval", { exact: true })).toBeVisible();
});

test("ops page shows workflow states and consumer lag", async ({ page }) => {
  await mockApi(page);
  await page.goto("/ops");
  await expect(page.getByTestId("states")).toContainText("awaiting_approval");
  await expect(page.getByTestId("lag")).toContainText("opspilot-triage");
  await expect(page.getByText("Approval rate: 80.0%")).toBeVisible();
});
