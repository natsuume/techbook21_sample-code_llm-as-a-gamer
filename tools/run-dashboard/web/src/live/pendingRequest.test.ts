// pendingRequest のテスト。最後の要求の後の応答待ちの区間と、区間を出さないときを確かめる
import { describe, expect, it } from "vitest";
import type { RangeResponse, RequestItem } from "../api/apiTypes.ts";
import { pendingRequestSpan } from "./pendingRequest.ts";

// テスト用の要求。受信時刻は送信時刻の 0.5 秒後 (null は失敗した要求)
const request = (
  n: number,
  tSent: number,
  tReceived: number | null = tSent + 0.5,
): RequestItem => ({
  n,
  t_sent: tSent,
  t_received: tReceived,
  result: tReceived === null ? "error" : "ok",
  action: null,
  advisor_requested: [],
  advisor_accepted: [],
  p: null,
  adopted_count: null,
});

const range = (items: RequestItem[], available = true): RangeResponse<RequestItem> => ({
  available,
  items,
  cursor: 0,
});

describe("pendingRequestSpan", () => {
  it("最後の要求の受信時刻から tEnd までを返す", () => {
    expect(pendingRequestSpan(range([request(1, 1), request(2, 2)]), 4)).toEqual({
      start: 2.5,
      end: 4,
    });
  });

  it("最後の要求が失敗した要求 (受信時刻が無い) なら、送信時刻から", () => {
    expect(pendingRequestSpan(range([request(1, 1), request(2, 3, null)]), 4)).toEqual({
      start: 3,
      end: 4,
    });
  });

  it("要求の記録が無いか、要求が無ければ null", () => {
    expect(pendingRequestSpan(range([request(1, 1)], false), 4)).toBeNull();
    expect(pendingRequestSpan(range([]), 4)).toBeNull();
  });

  it("tEnd が最後の要求の時刻以前か null なら null", () => {
    const requests = range([request(1, 1)]);
    expect(pendingRequestSpan(requests, 1.5)).toBeNull();
    expect(pendingRequestSpan(requests, 1.25)).toBeNull();
    expect(pendingRequestSpan(requests, null)).toBeNull();
  });

  it("t_sent が最大の要求ではなく、配列の最後の要求を使う", () => {
    expect(pendingRequestSpan(range([request(5, 10), request(6, 8)]), 12)).toEqual({
      start: 8.5,
      end: 12,
    });
  });
});
