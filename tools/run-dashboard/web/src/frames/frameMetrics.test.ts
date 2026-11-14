// frameMetrics のテスト。谷までの残り、区間のまとめ、画像に当たるフレーム、フレームより後の要求を確かめる
import { describe, expect, it } from "vitest";
import type { FrameItem, RequestItem } from "../api/apiTypes.ts";
import {
  frameIndexOfImage,
  requestsAfter,
  summarizeFrames,
  valleyDistance,
} from "./frameMetrics.ts";

// テスト用のフレーム。指定しないキーは、判定の出来事の無い rising のフレームの値にする
const frame = (fields: Partial<FrameItem>): FrameItem => ({
  t: 0,
  sharpness: 100,
  diff: null,
  phase: "rising",
  peak_t: null,
  peak_sharpness: 100,
  ref: 80,
  event: null,
  thumb: "thumbs/000000.jpg",
  ...fields,
});

// テスト用の要求。指定しないキーは、値の無い成功した要求の値にする
const request = (n: number, tSent: number): RequestItem => ({
  n,
  t_sent: tSent,
  t_received: tSent + 0.5,
  result: "ok",
  action: null,
  advisor_requested: [],
  advisor_accepted: [],
  p: null,
  adopted_count: null,
});

describe("valleyDistance", () => {
  it("rising は sharpness - ref (谷に入るまでの残り) を返す", () => {
    expect(valleyDistance({ phase: "rising", sharpness: 125.5, ref: 100.25 })).toEqual({
      phase: "rising",
      remaining: expect.closeTo(25.25),
    });
  });

  it("falling は ref - sharpness (谷を確定するまでの残り) を返す", () => {
    expect(valleyDistance({ phase: "falling", sharpness: 80.5, ref: 100.25 })).toEqual({
      phase: "falling",
      remaining: expect.closeTo(19.75),
    });
  });
});

describe("summarizeFrames", () => {
  it("区間に入るフレームの close・そのうち kept・fall の数を数える", () => {
    const frames = [
      frame({
        t: 1,
        event: {
          kind: "close",
          candidate_t: 0.8,
          candidate_sharpness: 60,
          kept: true,
          similar_diff: null,
        },
      }),
      frame({
        t: 2,
        event: {
          kind: "close",
          candidate_t: 1.8,
          candidate_sharpness: 55,
          kept: false,
          similar_diff: 0.01,
        },
      }),
      frame({ t: 3, event: { kind: "fall" } }),
      frame({ t: 4, event: null }),
    ];
    expect(summarizeFrames(frames, { from: 0.5, to: 5 })).toEqual({
      closes: 2,
      kept: 1,
      falls: 1,
      pendingPeak: null,
    });
  });

  it("t が null のフレームと区間の外のフレームは数えない", () => {
    const frames = [
      frame({ t: 0.2, event: { kind: "fall" } }),
      frame({ t: 1, event: { kind: "fall" } }),
      frame({
        t: null,
        event: {
          kind: "close",
          candidate_t: null,
          candidate_sharpness: 60,
          kept: true,
          similar_diff: null,
        },
      }),
      frame({ t: 9, event: { kind: "fall" } }),
    ];
    expect(summarizeFrames(frames, { from: 0.5, to: 5 })).toEqual({
      closes: 0,
      kept: 0,
      falls: 1,
      pendingPeak: null,
    });
  });

  it("区間の最後のフレームが falling なら、その山を確定待ちの山として返す", () => {
    const frames = [
      frame({ t: 1, phase: "rising" }),
      frame({ t: 2, phase: "falling", peak_t: 1.5, peak_sharpness: 150 }),
      frame({ t: 9, phase: "rising" }),
    ];
    expect(summarizeFrames(frames, { from: 0.5, to: 5 })).toEqual({
      closes: 0,
      kept: 0,
      falls: 0,
      pendingPeak: { t: 1.5, sharpness: 150 },
    });
  });

  it("確定待ちの山の peak_t が null なら t を null で返す", () => {
    const frames = [frame({ t: 2, phase: "falling", peak_t: null, peak_sharpness: 140 })];
    expect(summarizeFrames(frames, { from: 0.5, to: 5 })).toEqual({
      closes: 0,
      kept: 0,
      falls: 0,
      pendingPeak: { t: null, sharpness: 140 },
    });
  });

  it("区間の最後のフレームが rising なら、前のフレームが falling でも確定待ちの山は null", () => {
    const frames = [
      frame({ t: 1, phase: "falling", peak_t: 0.5, peak_sharpness: 150 }),
      frame({ t: 2, phase: "rising" }),
    ];
    expect(summarizeFrames(frames, { from: 0.5, to: 5 }).pendingPeak).toBeNull();
  });

  it("区間にフレームが無ければ、数はすべて 0 で確定待ちの山は null", () => {
    expect(summarizeFrames([frame({ t: 9, phase: "falling" })], { from: 0.5, to: 5 })).toEqual({
      closes: 0,
      kept: 0,
      falls: 0,
      pendingPeak: null,
    });
  });
});

describe("frameIndexOfImage", () => {
  const frames = [
    frame({ t: 1, thumb: "thumbs/a.jpg" }),
    frame({ t: 2, thumb: "thumbs/b.jpg" }),
    frame({ t: 3, thumb: "thumbs/c.jpg" }),
  ];
  const frameTimes = [1, 2, 3];

  it("thumb が一致するフレームがあれば、時刻に関わらずその index を返す", () => {
    expect(frameIndexOfImage(frames, frameTimes, { thumb: "thumbs/a.jpg", t: 2.9 })).toBe(0);
  });

  it("thumb が一致しなければ、画像の時刻以前の最後のフレームの index を返す", () => {
    expect(frameIndexOfImage(frames, frameTimes, { thumb: "thumbs/zzz.jpg", t: 3.5 })).toBe(2);
  });

  it("thumb が null なら、画像の時刻以前の最後のフレームの index を返す", () => {
    expect(frameIndexOfImage(frames, frameTimes, { thumb: null, t: 2.5 })).toBe(1);
  });

  it("thumb が一致せず、画像の時刻以前のフレームも無ければ -1 を返す", () => {
    expect(frameIndexOfImage(frames, frameTimes, { thumb: "thumbs/zzz.jpg", t: 0.5 })).toBe(-1);
    expect(frameIndexOfImage(frames, frameTimes, { thumb: null, t: 0.5 })).toBe(-1);
  });

  it("フレームが無ければ -1 を返す", () => {
    expect(frameIndexOfImage([], [], { thumb: "thumbs/a.jpg", t: 1 })).toBe(-1);
  });
});

describe("requestsAfter", () => {
  const requests = [request(1, 1), request(2, 2), request(3, 3), request(4, 4)];

  it("t_sent が t 以上の要求の n を返す (t と等しい要求を含める)", () => {
    expect(requestsAfter(requests, 2, 10)).toEqual([2, 3, 4]);
  });

  it("最大 limit 件を返す", () => {
    expect(requestsAfter(requests, 2, 2)).toEqual([2, 3]);
  });

  it("limit が 0 なら空の配列を返す", () => {
    expect(requestsAfter(requests, 2, 0)).toEqual([]);
  });

  it("当てはまる要求が無ければ空の配列を返す", () => {
    expect(requestsAfter(requests, 5, 3)).toEqual([]);
  });

  it("配列の順に返す", () => {
    const unordered = [request(5, 3), request(6, 1), request(7, 2)];
    expect(requestsAfter(unordered, 2, 10)).toEqual([5, 7]);
  });
});
