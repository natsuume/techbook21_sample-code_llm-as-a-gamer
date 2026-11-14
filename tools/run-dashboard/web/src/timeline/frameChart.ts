// 意味のあるフレームのレーンの折れ線 (鮮明度と谷とみなす値、直前のフレームとの差分) の縦の上限・点の座標と、
// カーソルの時刻に最も近いフレームを求める純粋な関数。座標は SVG の viewBox (幅 1000、高さ 100) の値。
import type { FrameItem } from "../api/apiTypes.ts";
import { lastIndexAtOrBefore } from "./rangeSearch.ts";
import type { TimeRange } from "./viewWindow.ts";

/** 折れ線の viewBox の幅 */
export const CHART_VIEW_WIDTH = 1000;
/** 折れ線の viewBox の高さ */
export const CHART_VIEW_HEIGHT = 100;
/** 鮮明度の縦の上限を、値の最大より上に空ける倍率 */
const SHARPNESS_HEADROOM = 1.08;
/** 差分の縦の上限を、上限にする値より上に空ける倍率 */
const DIFF_HEADROOM = 1.1;
/** 差分の縦の上限にする分位 (まれに大きくなる値で、ほかの値が潰れないため) */
const DIFF_QUANTILE = 0.99;

/** value を min〜max に切り詰める */
const clamp = (value: number, min: number, max: number): number =>
  Math.min(Math.max(value, min), max);

/** 小数 1 桁に丸める (points の文字を短くするため) */
const roundOne = (value: number): number => Math.round(value * 10) / 10;

/**
 * 鮮明度の折れ線の縦の上限を返す。frames の sharpness と ref の最大値 × 1.08。
 * frames が空か、最大値が 0 以下なら 1
 */
export const sharpnessScaleMax = (
  frames: readonly Pick<FrameItem, "sharpness" | "ref">[],
): number => {
  const largest = frames.reduce(
    (current, frame) => Math.max(current, frame.sharpness, frame.ref),
    0,
  );
  return largest > 0 ? largest * SHARPNESS_HEADROOM : 1;
};

/**
 * 差分の折れ線の縦の上限を返す。null でない diff を昇順に並べた 99% の位置の値
 * (位置は floor((件数 - 1) × 0.99)) と similarityThreshold の大きいほう × 1.1。0 以下なら 1。
 * 上限を超える差分は、折れ線の上端に切り詰めて描く
 */
export const diffScaleMax = (
  frames: readonly Pick<FrameItem, "diff">[],
  similarityThreshold: number,
): number => {
  const diffs = frames
    .map((frame) => frame.diff)
    .filter((diff): diff is number => diff !== null)
    .sort((left, right) => left - right);
  const quantile = diffs[Math.floor((diffs.length - 1) * DIFF_QUANTILE)] ?? 0;
  const largest = Math.max(quantile, similarityThreshold);
  return largest > 0 ? largest * DIFF_HEADROOM : 1;
};

/** 時刻 t の x (range の from が 0、to が 1000)。range の外の時刻は 0〜1000 の外になる */
export const chartX = (t: number, range: TimeRange): number =>
  ((t - range.from) / (range.to - range.from)) * CHART_VIEW_WIDTH;

/** 値 value の y (0 が下端の 100、max が上端の 0)。0〜max に切り詰める */
export const chartY = (value: number, max: number): number =>
  CHART_VIEW_HEIGHT - (clamp(value, 0, max) / max) * CHART_VIEW_HEIGHT;

/**
 * 折れ線の points 属性の値 ("x,y x,y …") の列を返す。値が null の点で線を切り、切れた区間ごとに 1 つの値にする。
 * 点が 1 つだけの区間は入れない (線にならないため)。x・y は小数 1 桁に丸める
 */
export const polylineSegments = <T extends { t: number }>(
  items: readonly T[],
  valueAt: (item: T) => number | null,
  range: TimeRange,
  max: number,
): string[] => {
  const segments: string[] = [];
  let points: string[] = [];
  const flush = (): void => {
    if (points.length >= 2) {
      segments.push(points.join(" "));
    }
    points = [];
  };
  for (const item of items) {
    const value = valueAt(item);
    if (value === null) {
      flush();
      continue;
    }
    points.push(`${roundOne(chartX(item.t, range))},${roundOne(chartY(value, max))}`);
  }
  flush();
  return segments;
};

/**
 * 昇順の sortedTimes の位置 start〜end - 1 のうち、t に最も近い位置を返す (同じ距離なら前の位置)。
 * start >= end なら -1
 */
export const nearestIndexWithin = (
  sortedTimes: readonly number[],
  start: number,
  end: number,
  t: number,
): number => {
  if (start >= end) {
    return -1;
  }
  const before = clamp(lastIndexAtOrBefore(sortedTimes, t), start, end - 1);
  const after = Math.min(before + 1, end - 1);
  const distance = (index: number): number =>
    Math.abs((sortedTimes[index] ?? Number.POSITIVE_INFINITY) - t);
  return distance(after) < distance(before) ? after : before;
};
