// 昇順に並んだ時刻の配列から、時刻や区間に当たる位置を二分探索 (O(log n)) で求める純粋な関数。
import type { TimeRange } from "./viewWindow.ts";

/**
 * sortedTimes[i] <= t を満たす最大の i を返す。無ければ -1。
 * sortedTimes は昇順 (同じ値の重複あり)。二分探索で求める
 */
export const lastIndexAtOrBefore: (sortedTimes: readonly number[], t: number) => number = () => {
  throw new Error("not implemented");
};

/**
 * from <= sortedTimes[i] <= to を満たす i の半開区間 [start, end) を返す。該当なしは start === end。
 * sortedTimes は昇順 (同じ値の重複あり)。二分探索で求める
 */
export const indexRangeWithin: (
  sortedTimes: readonly number[],
  range: TimeRange,
) => { start: number; end: number } = () => {
  throw new Error("not implemented");
};
