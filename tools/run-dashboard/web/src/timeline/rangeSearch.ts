// 昇順に並んだ時刻の配列から、時刻や区間に当たる位置を二分探索 (O(log n)) で求める純粋な関数。
import type { TimeRange } from "./viewWindow.ts";

/**
 * sortedTimes[i] が条件 belongs を満たす最初の i を返す (無ければ長さ)。
 * belongs は、ある位置で真になればそれより後でも真になる (昇順の配列で単調な) 条件
 */
const firstIndexWhere = (
  sortedTimes: readonly number[],
  belongs: (time: number) => boolean,
): number => {
  let low = 0;
  let high = sortedTimes.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    const time = sortedTimes[middle];
    if (time !== undefined && belongs(time)) {
      high = middle;
    } else {
      low = middle + 1;
    }
  }
  return low;
};

/**
 * sortedTimes[i] <= t を満たす最大の i を返す。無ければ -1。
 * sortedTimes は昇順 (同じ値の重複あり)。二分探索で求める
 */
export const lastIndexAtOrBefore = (sortedTimes: readonly number[], t: number): number =>
  firstIndexWhere(sortedTimes, (time) => time > t) - 1;

/**
 * from <= sortedTimes[i] <= to を満たす i の半開区間 [start, end) を返す。該当なしは start === end。
 * sortedTimes は昇順 (同じ値の重複あり)。二分探索で求める
 */
export const indexRangeWithin = (
  sortedTimes: readonly number[],
  range: TimeRange,
): { start: number; end: number } => {
  const start = firstIndexWhere(sortedTimes, (time) => time >= range.from);
  const end = Math.max(
    start,
    firstIndexWhere(sortedTimes, (time) => time > range.to),
  );
  return { start, end };
};
