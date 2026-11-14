// requestNavigation のテスト。要求の時刻、最初の要求、前後の要求、再生位置の求め方を確かめる
import { describe, expect, it } from "vitest";
import type { RequestItem } from "../api/apiTypes.ts";
import {
  adjacentRequestN,
  firstRequestN,
  playbackPosition,
  requestPosition,
} from "./requestNavigation.ts";

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

const requests = [
  request({ n: 3, t_sent: 1, t_received: 2.5 }),
  request({ n: 4, t_sent: 3, t_received: null, result: "error" }),
  request({ n: 5, t_sent: 4, t_received: 5.5 }),
];

describe("requestPosition", () => {
  it("受信時刻があれば受信時刻を返す", () => {
    expect(requestPosition({ t_sent: 1, t_received: 2.5 })).toBe(2.5);
  });

  it("受信時刻が null なら送信時刻を返す", () => {
    expect(requestPosition({ t_sent: 3, t_received: null })).toBe(3);
  });
});

describe("firstRequestN", () => {
  it("配列の最初の要求の n を返す", () => {
    expect(firstRequestN(requests)).toBe(3);
  });

  it("要求が無ければ null を返す", () => {
    expect(firstRequestN([])).toBeNull();
  });
});

describe("adjacentRequestN", () => {
  it("配列の順で次の要求の n を返す (失敗した要求も含める)", () => {
    expect(adjacentRequestN(requests, 3, 1)).toBe(4);
  });

  it("配列の順で前の要求の n を返す (失敗した要求も含める)", () => {
    expect(adjacentRequestN(requests, 5, -1)).toBe(4);
  });

  it("失敗した要求からも前後に動ける", () => {
    expect(adjacentRequestN(requests, 4, 1)).toBe(5);
    expect(adjacentRequestN(requests, 4, -1)).toBe(3);
  });

  it("最初の要求の前と最後の要求の次は null を返す", () => {
    expect(adjacentRequestN(requests, 3, -1)).toBeNull();
    expect(adjacentRequestN(requests, 5, 1)).toBeNull();
  });

  it("currentN の要求が無ければ null を返す", () => {
    expect(adjacentRequestN(requests, 99, 1)).toBeNull();
    expect(adjacentRequestN(requests, 99, -1)).toBeNull();
  });

  it("番号の大小ではなく配列の順で前後を決める", () => {
    const unordered = [
      request({ n: 10, t_sent: 1 }),
      request({ n: 2, t_sent: 2 }),
      request({ n: 7, t_sent: 3 }),
    ];
    expect(adjacentRequestN(unordered, 10, 1)).toBe(2);
    expect(adjacentRequestN(unordered, 7, -1)).toBe(2);
  });
});

describe("playbackPosition", () => {
  it("選んでいる要求があれば、その要求の時刻を返す", () => {
    expect(playbackPosition(request({ n: 3, t_sent: 1, t_received: 2.5 }), 100)).toBe(2.5);
    expect(playbackPosition(request({ n: 4, t_sent: 3, t_received: null }), 100)).toBe(3);
  });

  it("選んでいる要求が無ければ tEnd を返す", () => {
    expect(playbackPosition(null, 100)).toBe(100);
  });

  it("選んでいる要求が無く tEnd が null なら 0 を返す", () => {
    expect(playbackPosition(null, null)).toBe(0);
  });
});
