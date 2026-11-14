// 2 つの文の行の差分 (同じ行・足した行・消した行) を求める純粋な関数。

export type DiffLine = { kind: "same" | "add" | "del"; text: string };

/**
 * before と after を "\n" で行に分け、行の差分を返す。
 * - 最長共通部分列 (LCS) で same の行の数を最大にする
 * - 変わった箇所では del を add より前に並べる
 * - same と del の text を順に並べると before の行、same と add の text を順に並べると after の行に戻る
 */
export const diffLines: (before: string, after: string) => DiffLine[] = () => {
  throw new Error("not implemented");
};
