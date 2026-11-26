import { mkdirSync } from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { mockApi, RUN } from "./fixtures";

// Opt-in: `SCREENSHOTS=1 npx playwright test e2e/screenshots.spec.ts` writes docs/img/*.png.
// The API is mocked with the fixtures in ./fixtures.ts, so the images show the real UI with
// fixture data, not measured results.
const OUT = path.resolve(__dirname, "../../docs/img/frames");
test.skip(!process.env.SCREENSHOTS, "set SCREENSHOTS=1 to regenerate docs/img");
test.use({ viewport: { width: 1280, height: 800 } });

let frame = 0;
async function shot(page: Page, name: string) {
  mkdirSync(OUT, { recursive: true });
  frame += 1;
  await page.screenshot({ path: path.join(OUT, `${String(frame).padStart(2, "0")}-${name}.png`) });
}

test("walkthrough", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await expect(page.getByTestId("inbox-row")).toHaveCount(2);
  await shot(page, "inbox");

  await page.getByRole("link", { name: RUN.title }).click();
  await expect(page.getByTestId("verified")).toBeVisible();
  await shot(page, "issue");
  await page.getByTestId("duplicate").scrollIntoViewIfNeeded();
  await shot(page, "duplicates");

  await page.goto("/approvals");
  await page.getByLabel("Reviewer").fill("nicanor");
  await shot(page, "approvals");
  await page.getByRole("button", { name: "Edit" }).click();
  await page.getByLabel("Labels").fill("bug, investigate");
  await shot(page, "approvals-edit");
  await page.getByRole("button", { name: /Save edit/ }).click();
  await expect(page.getByTestId("decision-sent")).toBeVisible();
  await shot(page, "approvals-sent");

  await page.goto("/search");
  await page.getByLabel("Query").fill("dependency with yield raises RuntimeError at shutdown");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByTestId("results-hybrid_rerank")).toContainText("Dependencies with yield");
  await shot(page, "search");

  await page.goto("/evals");
  await expect(page.getByTestId("redteam")).toBeVisible();
  await shot(page, "evals");

  await page.goto("/ops");
  await expect(page.getByTestId("lag")).toBeVisible();
  await shot(page, "ops");
});
