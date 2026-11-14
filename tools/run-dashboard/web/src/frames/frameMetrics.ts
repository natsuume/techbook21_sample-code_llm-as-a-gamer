// フレームの判定 (鮮明度の谷の検出) の値から、谷までの残り・区間のまとめ・画像に当たるフレームを求める純粋な関数。
import type { FrameItem, RequestImage, RequestItem } from "../api/apiTypes.ts";
import type { TimeRange } from "../timeline/viewWindow.ts";

/** 谷に入る (rising) か、谷を確定する (falling) までの残りの鮮明度 */
export type ValleyDistance = { phase: "rising" | "falling"; remaining: number };

/**
 * 谷までの残りを返す。rising は sharpness - ref (谷に入るまでの残り)、
 * falling は ref - sharpness (谷を確定するまでの残り)
 */
export const valleyDistance: (
  frame: Pick<FrameItem, "phase" | "sharpness" | "ref">,
) => ValleyDistance = () => {
  throw new Error("not implemented");
};

/** 区間のフレームのまとめ。pendingPeak は確定待ちの山 */
export type FrameRangeSummary = {
  closes: number;
  kept: number;
  falls: number;
  pendingPeak: { t: number | null; sharpness: number } | null;
};

/**
 * t が null でなく range に入るフレームについて、event.kind が "close" の数 (closes)、そのうち kept が true の数
 * (kept)、event.kind が "fall" の数 (falls) を返す。pendingPeak は、範囲の最後のフレームが falling なら
 * その { t: peak_t, sharpness: peak_sharpness }、それ以外は null
 */
export const summarizeFrames: (
  frames: readonly FrameItem[],
  range: TimeRange,
) => FrameRangeSummary = () => {
  throw new Error("not implemented");
};

/**
 * 要求に載せた画像に当たるフレームの index を返す。thumb が一致するフレームがあればその index、無ければ
 * lastIndexAtOrBefore(frameTimes, image.t)。どちらも無ければ -1。
 * frameTimes は frames の t の配列 (呼び出し側が frames と同じ並びにそろえる)
 */
export const frameIndexOfImage: (
  frames: readonly FrameItem[],
  frameTimes: readonly number[],
  image: Pick<RequestImage, "thumb" | "t">,
) => number = () => {
  throw new Error("not implemented");
};

/** t_sent >= t の要求の n を、配列の順に最大 limit 件返す (フレームを載せた要求の候補) */
export const requestsAfter: (
  requests: readonly RequestItem[],
  t: number,
  limit: number,
) => number[] = () => {
  throw new Error("not implemented");
};
