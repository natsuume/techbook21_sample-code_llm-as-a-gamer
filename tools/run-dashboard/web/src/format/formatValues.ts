// 画面に表示する数・秒・大きさを文字列にする純粋な関数。数の書式は ja-JP。

/** 値が無いときに表示する文字 */
const MISSING_TEXT = "—";
const LOCALE = "ja-JP";
const BYTES_PER_KB = 1024;
const BYTES_PER_MB = 1024 * 1024;

/** 数を、小数 digits 桁の ja-JP の書式にする */
const formatFixed = (value: number, digits: number): string =>
  value.toLocaleString(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits });

/**
 * 秒を、小数 digits 桁 (既定 2) の ja-JP の書式で返す。null は "—"。
 * toLocaleString("ja-JP", { minimumFractionDigits: digits, maximumFractionDigits: digits }) を使う
 */
export const formatSeconds = (t: number | null, digits = 2): string =>
  t === null ? MISSING_TEXT : formatFixed(t, digits);

/**
 * バイト数を返す。null は "—"。1024 未満は "<n> B"、1024² 未満は "<x.x> KB"、それ以上は "<x.x> MB"
 * (KB と MB は小数 1 桁、ja-JP の書式)
 */
export const formatBytes = (bytes: number | null): string => {
  if (bytes === null) {
    return MISSING_TEXT;
  }
  if (bytes < BYTES_PER_KB) {
    return `${bytes} B`;
  }
  if (bytes < BYTES_PER_MB) {
    return `${formatFixed(bytes / BYTES_PER_KB, 1)} KB`;
  }
  return `${formatFixed(bytes / BYTES_PER_MB, 1)} MB`;
};

/** 文字数 (コードポイントの数、Array.from(text).length) を返す */
export const countCharacters = (text: string): number => Array.from(text).length;
