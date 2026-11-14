// タイムラインに表示する区間 (経過秒) を、再生位置と表示する幅から求める純粋な関数。

/** 経過秒の区間。from と to を含む */
export type TimeRange = { from: number; to: number };
/** 表示する幅 (秒)。"all" は実行の全体 */
export type ViewWidth = 10 | 30 | 120 | "all";
export const VIEW_WIDTHS: readonly ViewWidth[] = [10, 30, 120, "all"];

/** value を min〜max に切り詰める */
const clamp = (value: number, min: number, max: number): number =>
  Math.min(Math.max(value, min), max);

/**
 * 再生位置を中心にした表示の区間を返す。end = max(tEnd ?? 0, 0) とする。
 * - "all": { from: 0, to: end }。ただし end が 0 なら { from: 0, to: 1 }
 * - 数の幅 w: end <= w なら { from: 0, to: w }。それ以外は
 *   from = clamp(position - w / 2, 0, end - w)、to = from + w
 */
export const computeViewWindow = (
  position: number,
  width: ViewWidth,
  tEnd: number | null,
): TimeRange => {
  const end = Math.max(tEnd ?? 0, 0);
  if (width === "all") {
    return end === 0 ? { from: 0, to: 1 } : { from: 0, to: end };
  }
  if (end <= width) {
    return { from: 0, to: width };
  }
  const from = clamp(position - width / 2, 0, end - width);
  return { from, to: from + width };
};

/** 時刻 t の区間の中の位置 (t - from) / (to - from) を、0〜1 に切り詰めて返す */
export const ratioInRange = (t: number, range: TimeRange): number =>
  clamp((t - range.from) / (range.to - range.from), 0, 1);
