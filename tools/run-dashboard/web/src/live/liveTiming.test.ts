// liveTiming のテスト。時間の定数と、次に記録を足すまでの待ち・つなぎ直すまでの待ちを確かめる
import { describe, expect, it } from "vitest";
import {
  flushIntervalMs,
  LIVE_FLUSH_INTERVAL_ALL_MS,
  LIVE_FLUSH_INTERVAL_MS,
  nextFlushDelayMs,
  RECONNECT_MAX_MS,
  RUN_LIST_REFRESH_MS,
  reconnectDelayMs,
} from "./liveTiming.ts";

describe("時間の定数", () => {
  it("足す間隔は 250ms、一覧の読み直しは 5 秒、つなぎ直しの待ちの上限は 10 秒", () => {
    expect(LIVE_FLUSH_INTERVAL_MS).toBe(250);
    expect(RUN_LIST_REFRESH_MS).toBe(5000);
    expect(RECONNECT_MAX_MS).toBe(10000);
  });
});

describe("nextFlushDelayMs", () => {
  it("まだ足していなければ 0", () => {
    expect(nextFlushDelayMs(null, 5000)).toBe(0);
  });

  it("前に足してから 250ms たつまでの残りを返す", () => {
    expect(nextFlushDelayMs(1000, 1100)).toBe(150);
    expect(nextFlushDelayMs(1000, 1000)).toBe(250);
  });

  it("ちょうど 250ms か、それより後なら 0", () => {
    expect(nextFlushDelayMs(1000, 1250)).toBe(0);
    expect(nextFlushDelayMs(1000, 1300)).toBe(0);
  });

  it("間隔を渡せば、前に足してからその間隔がたつまでの残りを返す", () => {
    expect(nextFlushDelayMs(null, 5000, 1000)).toBe(0);
    expect(nextFlushDelayMs(1000, 1250, 1000)).toBe(750);
    expect(nextFlushDelayMs(1000, 2000, 1000)).toBe(0);
  });
});

describe("flushIntervalMs", () => {
  it("幅が「全体」なら 1 秒、それ以外は 250ms", () => {
    expect(LIVE_FLUSH_INTERVAL_ALL_MS).toBe(1000);
    expect(flushIntervalMs("all")).toBe(LIVE_FLUSH_INTERVAL_ALL_MS);
    for (const width of [10, 30, 120] as const) {
      expect(flushIntervalMs(width)).toBe(LIVE_FLUSH_INTERVAL_MS);
    }
  });
});

describe("reconnectDelayMs", () => {
  it("失敗が 0 回ならすぐ (0)、1 回目からは 1 秒・2 秒・4 秒・8 秒と倍にする", () => {
    expect(reconnectDelayMs(0)).toBe(0);
    expect(reconnectDelayMs(1)).toBe(1000);
    expect(reconnectDelayMs(2)).toBe(2000);
    expect(reconnectDelayMs(3)).toBe(4000);
    expect(reconnectDelayMs(4)).toBe(8000);
  });

  it("5 回以上は上限の 10 秒", () => {
    expect(reconnectDelayMs(5)).toBe(10000);
    expect(reconnectDelayMs(6)).toBe(10000);
    expect(reconnectDelayMs(100)).toBe(10000);
  });
});
