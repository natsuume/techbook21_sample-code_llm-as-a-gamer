import { describe, expect, it } from "vitest";
import { nextTabIndex } from "./tabKeyboard.ts";

describe("nextTabIndex", () => {
  it("右の矢印は次、最後なら最初", () => {
    expect(nextTabIndex("ArrowRight", 0, 5)).toBe(1);
    expect(nextTabIndex("ArrowRight", 4, 5)).toBe(0);
  });

  it("左の矢印は前、最初なら最後", () => {
    expect(nextTabIndex("ArrowLeft", 3, 5)).toBe(2);
    expect(nextTabIndex("ArrowLeft", 0, 5)).toBe(4);
  });

  it("Home は最初、End は最後", () => {
    expect(nextTabIndex("Home", 3, 5)).toBe(0);
    expect(nextTabIndex("End", 1, 5)).toBe(4);
  });

  it("ほかのキーとタブが無いときは null", () => {
    expect(nextTabIndex("Enter", 1, 5)).toBeNull();
    expect(nextTabIndex("ArrowRight", 0, 0)).toBeNull();
  });
});
