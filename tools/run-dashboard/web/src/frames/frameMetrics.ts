// フレームの判定 (鮮明度の谷の検出) の値から、谷までの残り・区間のまとめ・画像に当たるフレームを求める純粋な関数。
import type { FrameItem, RequestImage, RequestItem } from "../api/apiTypes.ts";
import { lastIndexAtOrBefore } from "../timeline/rangeSearch.ts";
import type { TimeRange } from "../timeline/viewWindow.ts";

/** 谷に入る (rising) か、谷を確定する (falling) までの残りの鮮明度 */
export type ValleyDistance = { phase: "rising" | "falling"; remaining: number };

/**
 * 谷までの残りを返す。rising は sharpness - ref (谷に入るまでの残り)、
 * falling は ref - sharpness (谷を確定するまでの残り)
 */
export const valleyDistance = (
  frame: Pick<FrameItem, "phase" | "sharpness" | "ref">,
): ValleyDistance => ({
  phase: frame.phase,
  remaining: frame.phase === "rising" ? frame.sharpness - frame.ref : frame.ref - frame.sharpness,
});

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
export const summarizeFrames = (
  frames: readonly FrameItem[],
  range: TimeRange,
): FrameRangeSummary => {
  let closes = 0;
  let kept = 0;
  let falls = 0;
  let lastFrame: FrameItem | null = null;
  for (const frame of frames) {
    const t = frame.t;
    if (t === null || t < range.from || t > range.to) {
      continue;
    }
    if (frame.event?.kind === "close") {
      closes += 1;
      if (frame.event.kept) {
        kept += 1;
      }
    } else if (frame.event?.kind === "fall") {
      falls += 1;
    }
    // 範囲の最後のフレームは時刻が最大のもの (同じ時刻なら配列の後のもの)
    if (lastFrame === null || lastFrame.t === null || t >= lastFrame.t) {
      lastFrame = frame;
    }
  }
  const pendingPeak =
    lastFrame?.phase === "falling"
      ? { t: lastFrame.peak_t, sharpness: lastFrame.peak_sharpness }
      : null;
  return { closes, kept, falls, pendingPeak };
};

/**
 * 要求に載せた画像に当たるフレームの index を返す。thumb が null でなければ thumb が一致するフレームの index
 * (無ければ -1)。thumb が null なら lastIndexAtOrBefore(frameTimes, image.t) (無ければ -1)。
 * thumb が一致しないのは、そのフレームの記録が捨てられたときなので、時刻の近い別のフレームを返さない。
 * frameTimes は frames の t の配列 (呼び出し側が frames と同じ並びにそろえる)
 */
export const frameIndexOfImage = (
  frames: readonly FrameItem[],
  frameTimes: readonly number[],
  image: Pick<RequestImage, "thumb" | "t">,
): number => {
  const thumb = image.thumb;
  if (thumb === null) {
    return lastIndexAtOrBefore(frameTimes, image.t);
  }
  return frames.findIndex((frame) => frame.thumb === thumb);
};

/** t_sent >= t の要求の n を、配列の順に最大 limit 件返す (フレームを載せた要求の候補) */
export const requestsAfter = (
  requests: readonly RequestItem[],
  t: number,
  limit: number,
): number[] => {
  const numbers: number[] = [];
  for (const request of requests) {
    if (numbers.length >= limit) {
      break;
    }
    if (request.t_sent >= t) {
      numbers.push(request.n);
    }
  }
  return numbers;
};
