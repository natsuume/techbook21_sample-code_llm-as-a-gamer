// 画面に表示する数・秒・大きさを文字列にする純粋な関数。数の書式は ja-JP。

/**
 * 秒を、小数 digits 桁 (既定 2) の ja-JP の書式で返す。null は "—"。
 * toLocaleString("ja-JP", { minimumFractionDigits: digits, maximumFractionDigits: digits }) を使う
 */
export const formatSeconds: (t: number | null, digits?: number) => string = () => {
  throw new Error("not implemented");
};

/**
 * バイト数を返す。null は "—"。1024 未満は "<n> B"、1024² 未満は "<x.x> KB"、それ以上は "<x.x> MB"
 * (KB と MB は小数 1 桁、ja-JP の書式)
 */
export const formatBytes: (bytes: number | null) => string = () => {
  throw new Error("not implemented");
};

/** 文字数 (コードポイントの数、Array.from(text).length) を返す */
export const countCharacters: (text: string) => number = () => {
  throw new Error("not implemented");
};
