import { describe, expect, it } from "vitest";

import { formatDuration, formatPercent } from "./format";

describe("formatPercent", () => {
  it("renders missing values as n/a", () => {
    expect(formatPercent(null)).toBe("n/a");
    expect(formatPercent(Number.NaN)).toBe("n/a");
  });
  it("scales fractions", () => {
    expect(formatPercent(0.8312, 1)).toBe("83.1%");
  });
});

describe("formatDuration", () => {
  it("picks a unit by magnitude", () => {
    expect(formatDuration(420)).toBe("420 ms");
    expect(formatDuration(4200)).toBe("4.2 s");
    expect(formatDuration(125_000)).toBe("2m 5s");
  });
});
