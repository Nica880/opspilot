import { describe, expect, it } from "vitest";

import { buildDecision } from "./api";
import { splitCitations, unknownCitations } from "./citations";

describe("splitCitations", () => {
  it("separates text and citations in order", () => {
    expect(splitCitations("Use Depends [S1] or yield [S2].")).toEqual([
      { text: "Use Depends " },
      { cite: "S1" },
      { text: " or yield " },
      { cite: "S2" },
      { text: "." },
    ]);
  });
  it("handles text without citations", () => {
    expect(splitCitations("plain")).toEqual([{ text: "plain" }]);
  });
  it("finds citations that point at no evidence", () => {
    expect(unknownCitations("[S1] [S3] [S3]", ["S1", "S2"])).toEqual(["S3"]);
  });
});

describe("buildDecision", () => {
  const original = { reply: "Hi", labels: ["bug"] };
  it("sends only the fields the reviewer changed", () => {
    expect(buildDecision("edit", "n", "Hi", "bug, docs", original)).toEqual({
      decision: "edit",
      reviewer: "n",
      edited: { labels: ["bug", "docs"] },
    });
  });
  it("ignores the form for approve and reject", () => {
    expect(buildDecision("reject", "n", "changed", "x", original)).toEqual({
      decision: "reject",
      reviewer: "n",
    });
  });
});
