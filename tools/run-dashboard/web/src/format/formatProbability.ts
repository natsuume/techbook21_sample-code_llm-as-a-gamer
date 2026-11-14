// 確率 (0〜1 の値) を、画面に出す文字列と棒の長さ (CSS の百分率) にする純粋な関数。

/** 値が無いときに表示する文字 */
const MISSING_TEXT = "—";

/** 確率を小数 2 桁の ja-JP の書式で返す。null は "—" */
export const formatProbability = (value: number | null): string =>
  value === null
    ? MISSING_TEXT
    : value.toLocaleString("ja-JP", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** 0〜1 の値を、0〜1 に切り詰めて CSS の百分率 ("<値 × 100>%") にする。数でない値は "0%" */
export const ratioToPercent = (ratio: number): string => {
  const clamped = Number.isFinite(ratio) ? Math.min(Math.max(ratio, 0), 1) : 0;
  return `${clamped * 100}%`;
};
