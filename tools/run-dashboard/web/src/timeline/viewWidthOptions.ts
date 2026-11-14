// タイムラインの幅の select の選択肢 (option の value と文言) と、選んだ value から幅を戻す純粋な関数。
import { VIEW_WIDTHS, type ViewWidth } from "./viewWindow.ts";

/** 幅の選択肢。value は option の value 属性の文字列、label は文言 */
export type ViewWidthOption = { width: ViewWidth; value: string; label: string };

/** 幅の文言。数の幅は 60 秒以上を分で、それ以外を秒で書く */
export const viewWidthLabel = (width: ViewWidth): string => {
  if (width === "all") {
    return "全体";
  }
  return width >= 60 && width % 60 === 0 ? `${width / 60} 分` : `${width} 秒`;
};

/** VIEW_WIDTHS の順の選択肢 (10 秒・30 秒・2 分・全体)。value は String(width) */
export const VIEW_WIDTH_OPTIONS: readonly ViewWidthOption[] = VIEW_WIDTHS.map((width) => ({
  width,
  value: String(width),
  label: viewWidthLabel(width),
}));

/** option の value から幅を返す。VIEW_WIDTH_OPTIONS に無い value は null */
export const parseViewWidth = (value: string): ViewWidth | null =>
  VIEW_WIDTH_OPTIONS.find((option) => option.value === value)?.width ?? null;
