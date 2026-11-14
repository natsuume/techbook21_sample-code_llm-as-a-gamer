// 詳細ペインのフレームのタブ (開くフレーム・前後の小さな画像・判定の状態・載せた要求) に出す値を求める純粋な関数。
import type { FrameItem, RequestDetail, RequestDetailOldFormat } from "../api/apiTypes.ts";
import type { ValleyDistance } from "../frames/frameMetrics.ts";
import { lastIndexAtOrBefore } from "../timeline/rangeSearch.ts";

/** 前後の小さな画像を、選んでいるフレームの前と後ろに何枚ずつ出すか */
export const NEIGHBOR_RADIUS = 3;

/** フレームを載せた要求の候補の数 (フレームの時刻以後に送った要求を、この数まで調べる) */
export const FRAME_REQUEST_CANDIDATES = 10;

/**
 * フレームのタブで開くフレームの位置 (frameTimes と同じ並びの位置) を返す。フレームが無ければ null。
 * frameIndex があれば 0〜count - 1 に切り詰める。無ければ再生位置以前の最後のフレーム
 * (lastIndexAtOrBefore(frameTimes, position))、それも無ければ最初のフレーム
 */
export const resolveFrameIndex = (
  frameIndex: number | null,
  frameTimes: readonly number[],
  position: number,
): number | null => {
  const count = frameTimes.length;
  if (count === 0) {
    return null;
  }
  if (frameIndex !== null) {
    return Math.min(Math.max(frameIndex, 0), count - 1);
  }
  return Math.max(lastIndexAtOrBefore(frameTimes, position), 0);
};

/** 前後の小さな画像の 1 枠。offset は選んでいるフレームからのずれ (-radius〜radius)、index はフレームの位置 */
export type NeighborSlot = { offset: number; index: number | null };

/**
 * index の前後 radius 枚ずつの枠を、offset が -radius から radius の順に返す (長さ 2 × radius + 1)。
 * フレームの位置が 0 未満か count 以上の枠は index が null (空きの枠)
 */
export const neighborFrameSlots = (index: number, count: number, radius: number): NeighborSlot[] =>
  Array.from({ length: radius * 2 + 1 }, (_, position) => {
    const offset = position - radius;
    const neighbor = index + offset;
    return { offset, index: neighbor >= 0 && neighbor < count ? neighbor : null };
  });

/** 数を小数 digits 桁の ja-JP の書式にする (鮮明度・差分など)。null は "—" */
export const formatDecimal = (value: number | null, digits: number): string =>
  value === null
    ? "—"
    : value.toLocaleString("ja-JP", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });

/** 判定の状態の文。rising は谷に入る前、falling は谷の中 (鮮明度が戻ると谷を確定する) */
export const framePhaseText = (phase: FrameItem["phase"]): string =>
  phase === "rising" ? "谷に入る前 (rising)" : "谷の中 (falling)";

/** valleyDistance の残りの見出し。rising は "谷に入るまで"、falling は "谷を確定するまで" */
export const valleyDistanceLabel = (distance: Pick<ValleyDistance, "phase">): string =>
  distance.phase === "rising" ? "谷に入るまで" : "谷を確定するまで";

/** 要求の詳細が、画像の縮小画像が thumb のものを載せているか */
const showsThumb = (detail: RequestDetail | RequestDetailOldFormat, thumb: string): boolean =>
  "images" in detail && detail.images.some((image) => image.thumb === thumb);

/**
 * 読めた要求の詳細のうち、縮小画像が thumb の画像を載せた要求の n を、配列の順に返す。
 * 読めていない (undefined) 詳細と、古い形の実行の応答は除く
 */
export const requestsShowingFrame = (
  details: readonly (RequestDetail | RequestDetailOldFormat | undefined)[],
  thumb: string,
): number[] =>
  details.flatMap((detail) =>
    detail !== undefined && "n" in detail && showsThumb(detail, thumb) ? [detail.n] : [],
  );
