// 要求の値 (操作に採った数・問いの値) を、0.5 秒ごとの列にまとめる純粋な関数。
// 列の番号 k の列は、経過秒 k * 0.5 から始まる。
import type { RequestItem } from "../api/apiTypes.ts";
import type { TimeRange } from "./viewWindow.ts";

/** 1 列の秒数 */
export const BIN_SECONDS = 0.5;
/** 1 列の値。owner は value を決めた要求の番号 n。該当する要求が無ければ value と owner は null */
export type BinCell = { start: number; value: number | null; owner: number | null };

/**
 * 区間を覆う列の番号の範囲を返す。first = floor(from / 0.5)、last = ceil(to / 0.5) - 1。
 * ただし last < first なら last = first
 */
export const binIndexRange: (range: TimeRange) => { first: number; last: number } = () => {
  throw new Error("not implemented");
};

/**
 * 列ごとの、操作に採った数の最大値を返す。列は first〜last の昇順。
 * 要求は floor(t_sent / 0.5) === k の列 k に入る。first〜last の外の列に入る要求は無視する (端の列に入る
 * 要求は、t_sent が from より前でも含める)。
 * 列ごとに、adopted_count が null でない要求の最大値を value、その最大値を持つ最初の要求 (配列の順) の
 * n を owner にする。該当なしは両方 null
 */
export const binAdoptedCounts: (requests: readonly RequestItem[], range: TimeRange) => BinCell[] =
  () => {
    throw new Error("not implemented");
  };

/**
 * 問いごと・列ごとの、問いの値の最大値を返す。結果は [問いの番号][列]。
 * 要求の p が null の要求と、p[i] が null の値は無視する。ほかは binAdoptedCounts と同じ規則
 */
export const binQuestionValues: (
  requests: readonly RequestItem[],
  questionCount: number,
  range: TimeRange,
) => BinCell[][] = () => {
  throw new Error("not implemented");
};
