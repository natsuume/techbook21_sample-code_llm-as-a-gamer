// textCache のテスト。同じオブジェクトの文を 1 回だけ作ることを確かめる
import { describe, expect, it } from "vitest";
import { cachedDescriber } from "./textCache.ts";

describe("cachedDescriber", () => {
  it("同じオブジェクトには、覚えた文を返して describe を呼ばない", () => {
    let calls = 0;
    const describeItem = cachedDescriber((item: { n: number }) => {
      calls += 1;
      return `#${item.n}`;
    });
    const item = { n: 1 };
    expect(describeItem(item)).toBe("#1");
    expect(describeItem(item)).toBe("#1");
    expect(calls).toBe(1);
  });

  it("別のオブジェクトには、中身が同じでも describe を呼ぶ", () => {
    let calls = 0;
    const describeItem = cachedDescriber((item: { n: number }) => {
      calls += 1;
      return `#${item.n}`;
    });
    describeItem({ n: 1 });
    describeItem({ n: 1 });
    expect(calls).toBe(2);
  });
});
