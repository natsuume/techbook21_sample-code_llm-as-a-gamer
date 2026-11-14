// requestWindow のテスト。t_sent の順でない配列からの切り出し、左にはみ出した棒、列の集計の結果が要求の配列を
// すべて渡したときと同じになることを確かめる
import { describe, expect, it } from "vitest";
import type { RequestItem } from "../api/apiTypes.ts";
import { binAdoptedCounts, binQuestionValues } from "./halfSecondBins.ts";
import {
  barEnd,
  buildRequestTimeIndex,
  requestsInBins,
  requestsOverlapping,
} from "./requestWindow.ts";

const request = (
  n: number,
  tSent: number,
  tReceived: number | null,
  adoptedCount: number | null = 1,
): RequestItem => ({
  n,
  t_sent: tSent,
  t_received: tReceived,
  result: tReceived === null ? "error" : "ok",
  action: null,
  advisor_requested: [],
  advisor_accepted: [],
  p: [adoptedCount ?? 0, 0.5],
  adopted_count: adoptedCount,
});

const numbers = (requests: readonly RequestItem[]): number[] => requests.map((item) => item.n);

describe("barEnd", () => {
  it("t_received があればそれを、無ければ t_sent を返す", () => {
    expect(barEnd(request(1, 2, 3))).toBe(3);
    expect(barEnd(request(1, 2, null))).toBe(2);
  });
});

describe("buildRequestTimeIndex", () => {
  it("配列の位置を t_sent の昇順に並べ、同じ t_sent は配列の順に残す", () => {
    const requests = [request(1, 5, 6), request(2, 1, 2), request(3, 5, 5.5), request(4, 3, 3.5)];
    const index = buildRequestTimeIndex(requests);
    expect(index.order).toEqual([1, 3, 0, 2]);
    expect(index.sentTimes).toEqual([1, 3, 5, 5]);
  });

  it("棒の長さの最大値を持つ。要求が無ければ 0", () => {
    expect(buildRequestTimeIndex([request(1, 1, 4), request(2, 5, 5.5)]).maxDuration).toBe(3);
    expect(buildRequestTimeIndex([]).maxDuration).toBe(0);
  });
});

describe("requestsOverlapping", () => {
  const requests = [
    request(1, 0, 1),
    request(2, 8, 11), // 左にはみ出す
    request(3, 12, 13),
    request(4, 19.5, 21), // 右にはみ出す
    request(5, 25, 26),
    request(6, 15, null), // 失敗した要求 (長さ 0)
  ];
  const index = buildRequestTimeIndex(requests);

  it("区間と重なる要求を、要求の配列の順で返す", () => {
    expect(numbers(requestsOverlapping(requests, index, { from: 10, to: 20 }))).toEqual([
      2, 3, 4, 6,
    ]);
  });

  it("区間の端で接する要求を含める", () => {
    expect(numbers(requestsOverlapping(requests, index, { from: 11, to: 12 }))).toEqual([2, 3]);
  });

  it("重なる要求が無ければ空を返す", () => {
    expect(requestsOverlapping(requests, index, { from: 30, to: 40 })).toEqual([]);
  });

  it("t_sent の順でない配列でも、配列の順で返す", () => {
    const unordered = [request(1, 14, 15), request(2, 11, 12), request(3, 3, 4)];
    const unorderedIndex = buildRequestTimeIndex(unordered);
    expect(numbers(requestsOverlapping(unordered, unorderedIndex, { from: 10, to: 20 }))).toEqual([
      1, 2,
    ]);
  });
});

describe("requestsInBins", () => {
  const requests = [
    request(1, 9.6, 10, 1), // 列 19 (9.5〜10) に入る。from より前だが端の列に入る
    request(2, 9.4, 9.8, 4), // 列 18 は区間の列の外
    request(3, 10.2, 10.4, 2),
    request(4, 12.9, 13, 3), // 区間の最後の列 25 (12.5〜13)
    request(5, 13, 13.2, 5), // 列 26 は区間の列の外
    request(6, 10.3, 10.5, 2),
  ];
  const index = buildRequestTimeIndex(requests);
  const range = { from: 9.7, to: 13 };

  it("区間の列に入りうる要求を、要求の配列の順で返す", () => {
    expect(numbers(requestsInBins(requests, index, range))).toEqual([1, 3, 4, 5, 6]);
  });

  it("binAdoptedCounts に渡すと、要求の配列をすべて渡したときと同じ結果になる", () => {
    expect(binAdoptedCounts(requestsInBins(requests, index, range), range)).toEqual(
      binAdoptedCounts(requests, range),
    );
  });

  it("binQuestionValues に渡すと、要求の配列をすべて渡したときと同じ結果になる", () => {
    expect(binQuestionValues(requestsInBins(requests, index, range), 2, range)).toEqual(
      binQuestionValues(requests, 2, range),
    );
  });
});
