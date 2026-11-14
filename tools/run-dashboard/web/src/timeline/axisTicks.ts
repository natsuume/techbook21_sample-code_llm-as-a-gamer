// タイムラインの時刻の軸に置く目盛り (経過秒) を、表示の区間から求める純粋な関数。
import type { TimeRange } from "./viewWindow.ts";

/** 目盛りの刻み (秒) の候補。小さい順 */
export const TICK_STEPS: readonly number[] = [
  0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600,
];

/**
 * 区間の目盛りの時刻を昇順で返す。
 * - 刻みは TICK_STEPS のうち (to - from) / 刻み <= 10 + 1e-9 を満たす最小の値。どれも満たさなければ、
 *   3600 の倍数で条件を満たす最小の値。1e-9 は、表示の区間を from + 幅 で作るときの丸めで (to - from) が
 *   幅をわずかに超えても、同じ幅なら同じ刻みを選ぶための許容誤差
 * - 目盛りは刻みの整数倍のうち from <= t <= to のもの。値は Math.round(t * 1e6) / 1e6 でそろえる
 */
export const computeAxisTicks: (range: TimeRange) => number[] = () => {
  throw new Error("not implemented");
};
