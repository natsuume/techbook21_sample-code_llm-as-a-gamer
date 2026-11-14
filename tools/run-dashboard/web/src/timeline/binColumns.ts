// 0.5 秒ごとの列 (halfSecondBins の BinCell) を canvas に描く位置と、カーソルの位置から列を引く純粋な関数。
import { BIN_SECONDS, type BinCell } from "./halfSecondBins.ts";
import type { TimeRange } from "./viewWindow.ts";

/** canvas の上の列の位置 (CSS の px)。x は左端、width は幅 */
export type ColumnRect = { x: number; width: number };

/** value を min〜max に切り詰める */
const clamp = (value: number, min: number, max: number): number =>
  Math.min(Math.max(value, min), max);

/**
 * 列ごとの、幅 trackWidth (px) の canvas の上の位置を、cells と同じ並びで返す。
 * 列 [start, start + 0.5) を range に切り詰めて px にし、左端を切り捨て・右端を切り上げて整数の px にそろえる
 * (隣の列との間に隙間を作らないため)。range の外の列は幅 0。range の幅が 0 以下なら、すべて { x: 0, width: 0 }
 */
export const binColumnRects = (
  cells: readonly Pick<BinCell, "start">[],
  range: TimeRange,
  trackWidth: number,
): ColumnRect[] => {
  const span = range.to - range.from;
  if (!(span > 0)) {
    return cells.map(() => ({ x: 0, width: 0 }));
  }
  const toX = (t: number): number => clamp((t - range.from) / span, 0, 1) * trackWidth;
  return cells.map((cell) => {
    const left = Math.floor(toX(cell.start));
    const right = Math.ceil(toX(cell.start + BIN_SECONDS));
    return { x: left, width: Math.max(0, right - left) };
  });
};

/**
 * トラックの幅に対する比率 ratio (0〜1 に切り詰める) の位置にある列の、cells の中の位置を返す。
 * 位置の時刻 t = from + ratio * 幅 の列 floor(t / 0.5) を、cells の最初の列からの差で求め、cells の範囲に
 * 切り詰める (右端 ratio === 1 は最後の列)。cells が空か range の幅が 0 以下なら -1
 */
export const binIndexAt = (
  cells: readonly Pick<BinCell, "start">[],
  range: TimeRange,
  ratio: number,
): number => {
  const first = cells[0];
  const span = range.to - range.from;
  if (first === undefined || !(span > 0)) {
    return -1;
  }
  const t = range.from + clamp(ratio, 0, 1) * span;
  const offset = Math.floor(t / BIN_SECONDS) - Math.round(first.start / BIN_SECONDS);
  return clamp(offset, 0, cells.length - 1);
};
