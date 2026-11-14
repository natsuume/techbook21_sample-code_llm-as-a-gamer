// rangeSearch のテスト。重複を含む昇順の時刻での位置の求め方と、二分探索で要素を読む回数を確かめる
import { describe, expect, it } from "vitest";
import { indexRangeWithin, lastIndexAtOrBefore } from "./rangeSearch.ts";

// 配列の要素を読んだ回数を数える代理。二分探索なら、読む回数は配列の長さの log に比例する
const countingArray = (values: number[]): { array: readonly number[]; reads: () => number } => {
  let count = 0;
  const array = new Proxy(values, {
    get: (target, property, receiver) => {
      if (typeof property === "string" && /^\d+$/.test(property)) {
        count += 1;
      }
      return Reflect.get(target, property, receiver);
    },
  });
  return { array, reads: () => count };
};

const LARGE_LENGTH = 100000;
// 二分探索で読む回数の上限 (log2(100000) は約 17)。線形の探索ならこれを大きく越える
const MAX_READS = 100;
const largeTimes = (): number[] => Array.from({ length: LARGE_LENGTH }, (_, index) => index * 0.1);

describe("lastIndexAtOrBefore", () => {
  const times = [1, 2, 2, 2, 5, 8];

  it("t 以下の要素のうち最大の位置を返す", () => {
    expect(lastIndexAtOrBefore(times, 6)).toBe(4);
  });

  it("t と等しい要素が重複していれば、その最後の位置を返す", () => {
    expect(lastIndexAtOrBefore(times, 2)).toBe(3);
  });

  it("t が最後の要素以上なら最後の位置を返す", () => {
    expect(lastIndexAtOrBefore(times, 8)).toBe(5);
    expect(lastIndexAtOrBefore(times, 100)).toBe(5);
  });

  it("t が最初の要素より前なら -1 を返す", () => {
    expect(lastIndexAtOrBefore(times, 0.5)).toBe(-1);
  });

  it("空の配列なら -1 を返す", () => {
    expect(lastIndexAtOrBefore([], 3)).toBe(-1);
  });

  it("二分探索で求める (要素を読む回数が配列の長さの log 程度)", () => {
    const { array, reads } = countingArray(largeTimes());
    expect(lastIndexAtOrBefore(array, 1234.56)).toBe(12345);
    expect(reads()).toBeLessThanOrEqual(MAX_READS);
  });
});

describe("indexRangeWithin", () => {
  const times = [1, 2, 2, 2, 5, 8];

  it("区間に入る要素の位置の半開区間を返す", () => {
    expect(indexRangeWithin(times, { from: 1.5, to: 6 })).toEqual({ start: 1, end: 5 });
  });

  it("区間の両端と等しい要素を含める (重複もすべて含める)", () => {
    expect(indexRangeWithin(times, { from: 2, to: 5 })).toEqual({ start: 1, end: 5 });
  });

  it("区間が配列の全体を覆うなら [0, 長さ) を返す", () => {
    expect(indexRangeWithin(times, { from: 0, to: 10 })).toEqual({ start: 0, end: 6 });
  });

  it("区間に入る要素が無ければ start と end が等しい", () => {
    const between = indexRangeWithin(times, { from: 5.5, to: 7 });
    expect(between.start).toBe(between.end);
    const before = indexRangeWithin(times, { from: -3, to: 0 });
    expect(before.start).toBe(before.end);
    const after = indexRangeWithin(times, { from: 9, to: 12 });
    expect(after.start).toBe(after.end);
  });

  it("空の配列なら start と end が等しい", () => {
    const result = indexRangeWithin([], { from: 0, to: 10 });
    expect(result.start).toBe(result.end);
  });

  it("二分探索で求める (要素を読む回数が配列の長さの log 程度)", () => {
    const { array, reads } = countingArray(largeTimes());
    expect(indexRangeWithin(array, { from: 1000.05, to: 2000.05 })).toEqual({
      start: 10001,
      end: 20001,
    });
    expect(reads()).toBeLessThanOrEqual(MAX_READS);
  });
});
