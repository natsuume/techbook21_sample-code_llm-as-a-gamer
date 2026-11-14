// halfSecondBins のテスト。列の番号の範囲と、列ごとの最大値とその要求の選び方を確かめる
import { describe, expect, it } from "vitest";
import type { RequestItem } from "../api/apiTypes.ts";
import { binAdoptedCounts, binIndexRange, binQuestionValues } from "./halfSecondBins.ts";

// テスト用の要求。指定しないキーは、値の無い成功した要求の値にする
const request = (
  fields: Partial<RequestItem> & Pick<RequestItem, "n" | "t_sent">,
): RequestItem => ({
  t_received: null,
  result: "ok",
  action: null,
  advisor_requested: [],
  advisor_accepted: [],
  p: null,
  adopted_count: null,
  ...fields,
});

describe("binIndexRange", () => {
  it("区間を覆う列の番号の範囲を返す", () => {
    expect(binIndexRange({ from: 0, to: 2 })).toEqual({ first: 0, last: 3 });
    expect(binIndexRange({ from: 85, to: 115 })).toEqual({ first: 170, last: 229 });
  });

  it("区間の端が列の境でなければ、端を含む列まで広げる", () => {
    expect(binIndexRange({ from: 0.2, to: 1.3 })).toEqual({ first: 0, last: 2 });
  });

  it("幅が 0 の区間で last が first より小さくなるときは last を first にする", () => {
    expect(binIndexRange({ from: 1, to: 1 })).toEqual({ first: 2, last: 2 });
  });
});

describe("binAdoptedCounts", () => {
  it("列ごとに、操作に採った数の最大値とそれを持つ最初の要求を返す", () => {
    const requests = [
      request({ n: 1, t_sent: 0.1, adopted_count: 2 }),
      request({ n: 2, t_sent: 0.3, adopted_count: 5 }),
      request({ n: 3, t_sent: 0.4, adopted_count: 5 }),
      request({ n: 5, t_sent: 1.2, adopted_count: 0 }),
    ];
    expect(binAdoptedCounts(requests, { from: 0, to: 2 })).toEqual([
      { start: 0, value: 5, owner: 2 },
      { start: 0.5, value: null, owner: null },
      { start: 1, value: 0, owner: 5 },
      { start: 1.5, value: null, owner: null },
    ]);
  });

  it("最大値が同じ要求が複数あれば、配列の順で最初の要求を owner にする", () => {
    const requests = [
      request({ n: 7, t_sent: 0.4, adopted_count: 3 }),
      request({ n: 6, t_sent: 0.2, adopted_count: 3 }),
    ];
    expect(binAdoptedCounts(requests, { from: 0, to: 0.5 })).toEqual([
      { start: 0, value: 3, owner: 7 },
    ]);
  });

  it("adopted_count が null の要求は無視する", () => {
    const requests = [
      request({ n: 1, t_sent: 0.1, adopted_count: null }),
      request({ n: 2, t_sent: 0.6, adopted_count: null }),
      request({ n: 3, t_sent: 0.7, adopted_count: 1 }),
    ];
    expect(binAdoptedCounts(requests, { from: 0, to: 1 })).toEqual([
      { start: 0, value: null, owner: null },
      { start: 0.5, value: 1, owner: 3 },
    ]);
  });

  it("t_sent が列の始まりと等しい要求は、その列に入れる", () => {
    const requests = [
      request({ n: 1, t_sent: 0.49, adopted_count: 1 }),
      request({ n: 2, t_sent: 0.5, adopted_count: 4 }),
    ];
    expect(binAdoptedCounts(requests, { from: 0, to: 1 })).toEqual([
      { start: 0, value: 1, owner: 1 },
      { start: 0.5, value: 4, owner: 2 },
    ]);
  });

  it("区間の外の要求は無視する", () => {
    const requests = [
      request({ n: 1, t_sent: 0.2, adopted_count: 9 }),
      request({ n: 2, t_sent: 1.6, adopted_count: 1 }),
      request({ n: 3, t_sent: 2.6, adopted_count: 9 }),
    ];
    expect(binAdoptedCounts(requests, { from: 1, to: 2 })).toEqual([
      { start: 1, value: null, owner: null },
      { start: 1.5, value: 1, owner: 2 },
    ]);
  });

  it("端の列に入る要求は、t_sent が from より前でも含める", () => {
    const requests = [
      request({ n: 1, t_sent: 1.1, adopted_count: 3 }),
      request({ n: 2, t_sent: 1.4, adopted_count: 1 }),
    ];
    expect(binAdoptedCounts(requests, { from: 1.3, to: 1.5 })).toEqual([
      { start: 1, value: 3, owner: 1 },
    ]);
  });

  it("to が列の境なら、t_sent === to の要求は次の列に入るため含めない", () => {
    const requests = [
      request({ n: 1, t_sent: 0.7, adopted_count: 1 }),
      request({ n: 2, t_sent: 1, adopted_count: 4 }),
    ];
    expect(binAdoptedCounts(requests, { from: 0.5, to: 1 })).toEqual([
      { start: 0.5, value: 1, owner: 1 },
    ]);
  });

  it("要求が無ければ、すべての列の値を null にする", () => {
    expect(binAdoptedCounts([], { from: 0, to: 1 })).toEqual([
      { start: 0, value: null, owner: null },
      { start: 0.5, value: null, owner: null },
    ]);
  });
});

describe("binQuestionValues", () => {
  it("[問いの番号][列] の形で、問いの値の最大値とそれを持つ最初の要求を返す", () => {
    const requests = [
      request({ n: 1, t_sent: 0.1, p: [0.2, 0.9] }),
      request({ n: 2, t_sent: 0.2, p: [0.6, 0.4] }),
      request({ n: 4, t_sent: 0.6, p: [0.6, 0.1] }),
      request({ n: 5, t_sent: 0.8, p: [0.6, 0.3] }),
    ];
    expect(binQuestionValues(requests, 2, { from: 0, to: 1 })).toEqual([
      [
        { start: 0, value: 0.6, owner: 2 },
        { start: 0.5, value: 0.6, owner: 4 },
      ],
      [
        { start: 0, value: 0.9, owner: 1 },
        { start: 0.5, value: 0.3, owner: 5 },
      ],
    ]);
  });

  it("p が null の要求は無視する", () => {
    const requests = [
      request({ n: 1, t_sent: 0.1, p: null }),
      request({ n: 2, t_sent: 0.2, p: [0.3, 0.4] }),
    ];
    expect(binQuestionValues(requests, 2, { from: 0, to: 0.5 })).toEqual([
      [{ start: 0, value: 0.3, owner: 2 }],
      [{ start: 0, value: 0.4, owner: 2 }],
    ]);
  });

  it("p の中の null の値は無視する", () => {
    const requests = [
      request({ n: 1, t_sent: 0.1, p: [null, 0.4] }),
      request({ n: 2, t_sent: 0.2, p: [0.3, null] }),
      request({ n: 3, t_sent: 0.7, p: [null, null] }),
    ];
    expect(binQuestionValues(requests, 2, { from: 0, to: 1 })).toEqual([
      [
        { start: 0, value: 0.3, owner: 2 },
        { start: 0.5, value: null, owner: null },
      ],
      [
        { start: 0, value: 0.4, owner: 1 },
        { start: 0.5, value: null, owner: null },
      ],
    ]);
  });

  it("区間の外の要求は無視する", () => {
    const requests = [
      request({ n: 1, t_sent: 0.2, p: [0.9] }),
      request({ n: 2, t_sent: 5, p: [1] }),
    ];
    expect(binQuestionValues(requests, 1, { from: 0, to: 0.5 })).toEqual([
      [{ start: 0, value: 0.9, owner: 1 }],
    ]);
  });
});
