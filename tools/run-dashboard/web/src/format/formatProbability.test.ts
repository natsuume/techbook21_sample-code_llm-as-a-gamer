import { describe, expect, it } from "vitest";
import { formatProbability, ratioToPercent } from "./formatProbability.ts";

describe("formatProbability", () => {
  it("小数 2 桁にする", () => {
    expect(formatProbability(0.875)).toBe("0.88");
    expect(formatProbability(1)).toBe("1.00");
    expect(formatProbability(0)).toBe("0.00");
  });

  it("null は「—」", () => {
    expect(formatProbability(null)).toBe("—");
  });
});

describe("ratioToPercent", () => {
  it("0〜1 の値を百分率にする", () => {
    expect(ratioToPercent(0.25)).toBe("25%");
    expect(ratioToPercent(1)).toBe("100%");
  });

  it("0〜1 の外は切り詰め、数でない値は 0%", () => {
    expect(ratioToPercent(-0.5)).toBe("0%");
    expect(ratioToPercent(1.5)).toBe("100%");
    expect(ratioToPercent(Number.NaN)).toBe("0%");
  });
});
