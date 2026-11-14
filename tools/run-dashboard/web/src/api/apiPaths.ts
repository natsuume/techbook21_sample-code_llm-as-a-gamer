// run-dashboard のサーバーの API のパスを組み立てる純粋な関数。
// 実行の名前と advisor の実行の名前は encodeURIComponent で符号化する。

export type RangeKind = "requests" | "frames" | "pad" | "events";

/** 実行の一覧のパス "/api/runs" を返す */
export const runsPath: () => string = () => {
  throw new Error("not implemented");
};

/** 実行の summary のパス "/api/runs/<run>" を返す。run は encodeURIComponent で符号化する */
export const summaryPath: (run: string) => string = () => {
  throw new Error("not implemented");
};

/**
 * 区間 API のパス "/api/runs/<run>/<kind>" を返す。from・to は付けない。
 * run は encodeURIComponent で符号化する
 */
export const rangePath: (run: string, kind: RangeKind) => string = () => {
  throw new Error("not implemented");
};

/** 要求の詳細のパス "/api/runs/<run>/requests/<n>" を返す。run は encodeURIComponent で符号化する */
export const requestDetailPath: (run: string, n: number) => string = () => {
  throw new Error("not implemented");
};

/** ゲームの情報の版のパス "/api/runs/<run>/notes/<version>" を返す。run は encodeURIComponent で符号化する */
export const notePath: (run: string, version: number) => string = () => {
  throw new Error("not implemented");
};

/**
 * advisor の実行のパス "/api/runs/<run>/advisor/<name>" を返す。
 * run と name は encodeURIComponent で符号化する
 */
export const advisorPath: (run: string, name: string) => string = () => {
  throw new Error("not implemented");
};

/**
 * 実行のディレクトリーのファイルのパス "/api/runs/<run>/files/<path>" を返す。
 * run は encodeURIComponent で符号化する。path は "/" で分け、部分ごとに encodeURIComponent で
 * 符号化して "/" でつなぐ ("/" を %2F にしない)
 */
export const filePath: (run: string, path: string) => string = () => {
  throw new Error("not implemented");
};
