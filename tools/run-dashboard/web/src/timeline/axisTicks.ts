// タイムラインの時刻の軸に置く目盛り (経過秒) を、表示の区間から求める純粋な関数。
import type { TimeRange } from "./viewWindow.ts";

/** 目盛りの刻み (秒) の候補。小さい順 */
export const TICK_STEPS: readonly number[] = [
  0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600,
];

/** 1 つの区間に置く目盛りの区切りの数の上限 */
const MAX_DIVISIONS = 10;
/** 区切りの数を上限と比べるときの、丸めの許容誤差 */
const DIVISION_TOLERANCE = 1e-9;
/** TICK_STEPS のどれでも足りないときに、倍数を刻みにする秒数 */
const LARGE_STEP_UNIT = 3600;

/** 幅 width を刻み step で区切った数が、上限以下か */
const fitsDivisions = (width: number, step: number): boolean =>
  width / step <= MAX_DIVISIONS + DIVISION_TOLERANCE;

/** 幅 width の区間に使う刻みを選ぶ */
const chooseStep = (width: number): number => {
  const listed = TICK_STEPS.find((step) => fitsDivisions(width, step));
  if (listed !== undefined) {
    return listed;
  }
  let multiple = Math.max(1, Math.floor(width / (LARGE_STEP_UNIT * MAX_DIVISIONS)));
  while (!fitsDivisions(width, LARGE_STEP_UNIT * multiple)) {
    multiple += 1;
  }
  return LARGE_STEP_UNIT * multiple;
};

/** 浮動小数の誤差を、小数 6 桁でそろえる */
const roundTick = (t: number): number => Math.round(t * 1e6) / 1e6;

/**
 * 区間の目盛りの時刻を昇順で返す。
 * - 刻みは TICK_STEPS のうち (to - from) / 刻み <= 10 + 1e-9 を満たす最小の値。どれも満たさなければ、
 *   3600 の倍数で条件を満たす最小の値。1e-9 は、表示の区間を from + 幅 で作るときの丸めで (to - from) が
 *   幅をわずかに超えても、同じ幅なら同じ刻みを選ぶための許容誤差
 * - 目盛りは刻みの整数倍のうち from <= t <= to のもの。値は Math.round(t * 1e6) / 1e6 でそろえる
 * - 区間の端が有限の数でなければ [] を返す (刻みを選べないため)
 */
export const computeAxisTicks = (range: TimeRange): number[] => {
  if (!Number.isFinite(range.from) || !Number.isFinite(range.to)) {
    return [];
  }
  const step = chooseStep(range.to - range.from);
  const ticks: number[] = [];
  for (let index = Math.ceil(range.from / step); index * step <= range.to; index += 1) {
    const t = index * step;
    if (t >= range.from) {
      ticks.push(roundTick(t));
    }
  }
  return ticks;
};
