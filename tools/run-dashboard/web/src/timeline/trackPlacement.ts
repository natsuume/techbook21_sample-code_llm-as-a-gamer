// タイムラインのトラックの上に、時刻の区間や点を置く位置 (トラックの幅に対する比率) を求める純粋な関数。
import type { TimeRange } from "./viewWindow.ts";

/** トラックの上の区間の位置。left は左端、width は幅で、どちらもトラックの幅に対する比率 (0〜1) */
export type SpanPlacement = { left: number; width: number };

/** 再生位置のラベルを置く側。right は線の右、left は線の左 */
export type PlayheadLabelSide = "right" | "left";

/** 再生位置のラベルを線の左に置く、トラックの右端からの比率のしきい値 */
const PLAYHEAD_LEFT_LABEL_RATIO = 0.85;

/**
 * 区間 [start, end] を表示の区間 range に切り詰めた位置を返す。
 * - range と重ならない (end < range.from か start > range.to) ときと、range の幅が 0 以下のときは null
 * - 切り詰めた区間を s = max(start, from)、e = max(s, min(end, to)) として、left = (s - from) / 幅、
 *   width = (e - s) / 幅。長さ 0 の区間 (start === end) は width 0 で返す (最小の幅は呼び出し側の CSS で決める)
 */
export const placeSpan = (start: number, end: number, range: TimeRange): SpanPlacement | null => {
  const span = range.to - range.from;
  if (!(span > 0) || end < range.from || start > range.to) {
    return null;
  }
  const clippedStart = Math.max(start, range.from);
  const clippedEnd = Math.max(clippedStart, Math.min(end, range.to));
  return {
    left: (clippedStart - range.from) / span,
    width: (clippedEnd - clippedStart) / span,
  };
};

/** 時刻 t の位置 (t - from) / 幅 を返す。t が range の外 (両端は含む) か、range の幅が 0 以下なら null */
export const placePoint = (t: number, range: TimeRange): number | null => {
  const span = range.to - range.from;
  if (!(span > 0) || t < range.from || t > range.to) {
    return null;
  }
  return (t - range.from) / span;
};

/** トラックの幅に対する比率 ratio (0〜1 に切り詰める) の位置の時刻 from + ratio × 幅 を返す */
export const timeAtRatio = (ratio: number, range: TimeRange): number =>
  range.from + Math.min(Math.max(ratio, 0), 1) * (range.to - range.from);

/** 区間 [start, end) が時刻 t を含むものを、渡した順のまま返す */
export const spansContaining = <T extends { start: number; end: number }>(
  spans: readonly T[],
  t: number,
): T[] => spans.filter((span) => span.start <= t && t < span.end);

/** 再生位置の比率 ratio が 0.85 より大きければ、ラベルを線の左に置く (右端ではみ出さないため) */
export const playheadLabelSide = (ratio: number): PlayheadLabelSide =>
  ratio > PLAYHEAD_LEFT_LABEL_RATIO ? "left" : "right";
