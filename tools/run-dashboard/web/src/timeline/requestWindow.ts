// 要求の一覧から、表示の区間に描く要求 (棒と 0.5 秒ごとの列) を二分探索で切り出す純粋な関数。
// 要求の配列は t_sent の順とは限らないため、t_sent の昇順の並びを別に作って探す。
import type { RequestItem } from "../api/apiTypes.ts";
import { BIN_SECONDS, binIndexRange } from "./halfSecondBins.ts";
import { indexRangeWithin } from "./rangeSearch.ts";
import type { TimeRange } from "./viewWindow.ts";

/**
 * 要求を t_sent で探すための並び。order は要求の配列の位置を t_sent の昇順 (同じ t_sent は配列の順) に並べたもの、
 * sentTimes[i] は requests[order[i]].t_sent。maxDuration は棒の長さ (barEnd - t_sent) の最大値 (要求が無ければ 0)
 */
export type RequestTimeIndex = { order: number[]; sentTimes: number[]; maxDuration: number };

/** 要求の棒の右端の時刻。t_received ?? t_sent */
export const barEnd = (request: Pick<RequestItem, "t_sent" | "t_received">): number =>
  request.t_received ?? request.t_sent;

/** 要求の配列から、t_sent で探すための並びを作る (RequestTimeIndex を参照) */
export const buildRequestTimeIndex = (requests: readonly RequestItem[]): RequestTimeIndex => {
  // Array.prototype.sort は安定なため、同じ t_sent の要求は配列の順のまま残る
  const order = requests
    .map((_, index) => index)
    .sort((left, right) => (requests[left]?.t_sent ?? 0) - (requests[right]?.t_sent ?? 0));
  const sentTimes = order.map((index) => requests[index]?.t_sent ?? 0);
  const maxDuration = requests.reduce(
    (largest, request) => Math.max(largest, barEnd(request) - request.t_sent),
    0,
  );
  return { order, sentTimes, maxDuration };
};

/** from <= t_sent <= to の要求を、要求の配列の順で返す */
const requestsSentBetween = (
  requests: readonly RequestItem[],
  index: RequestTimeIndex,
  from: number,
  to: number,
): RequestItem[] => {
  const { start, end } = indexRangeWithin(index.sentTimes, { from, to });
  const positions = index.order.slice(start, end).sort((left, right) => left - right);
  const found: RequestItem[] = [];
  for (const position of positions) {
    const request = requests[position];
    if (request !== undefined) {
      found.push(request);
    }
  }
  return found;
};

/**
 * 棒 [t_sent, barEnd] が表示の区間 range と重なる (t_sent <= to かつ barEnd >= from) 要求を、要求の配列の順で返す。
 * index は同じ requests から buildRequestTimeIndex で作ったもの。t_sent が from - maxDuration 以上の要求だけを
 * 二分探索で選んでから、重なりを確かめる
 */
export const requestsOverlapping = (
  requests: readonly RequestItem[],
  index: RequestTimeIndex,
  range: TimeRange,
): RequestItem[] =>
  requestsSentBetween(requests, index, range.from - index.maxDuration, range.to).filter(
    (request) => barEnd(request) >= range.from,
  );

/**
 * 表示の区間 range の 0.5 秒ごとの列 (binIndexRange(range) の first〜last) に入る要求の候補を、要求の配列の順で返す。
 * first * 0.5 <= t_sent <= (last + 1) * 0.5 の要求で、binAdoptedCounts・binQuestionValues に渡すと、要求の配列を
 * すべて渡したときと同じ結果になる (列の外の要求は、それらの関数が無視する)
 */
export const requestsInBins = (
  requests: readonly RequestItem[],
  index: RequestTimeIndex,
  range: TimeRange,
): RequestItem[] => {
  const { first, last } = binIndexRange(range);
  return requestsSentBetween(requests, index, first * BIN_SECONDS, (last + 1) * BIN_SECONDS);
};
