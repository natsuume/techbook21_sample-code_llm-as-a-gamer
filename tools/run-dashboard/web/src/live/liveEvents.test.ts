// liveEvents のテスト。イベントの名前と data の文字列から LiveEvent を読み、読めないものを null にすることを確かめる
import { describe, expect, it } from "vitest";
import { LIVE_EVENT_KINDS, type LiveEventKind } from "../api/apiTypes.ts";
import { parseLiveEvent } from "./liveEvents.ts";

// 種類ごとの data (サーバーが送る形)
const dataByKind: Record<LiveEventKind, Record<string, unknown>> = {
  request: {
    n: 3,
    t_sent: 1.25,
    t_received: 1.75,
    result: "ok",
    action: "押下 A / 短押し none",
    advisor_requested: [],
    advisor_accepted: [],
    p: [0.9, null],
    adopted_count: 1,
  },
  frame: {
    t: 2.5,
    sharpness: 120.5,
    diff: null,
    phase: "rising",
    peak_t: null,
    peak_sharpness: 120.5,
    ref: 80,
    event: null,
    thumb: "thumbs/000001.jpg",
  },
  pad: { t: 1.8, source: "apply", state: "A", ok: true, error: null },
  run: { t: -0.5, event: "record_error", error: "x" },
  note_version: { version: 1, t: 4, request: 3, initial: false, changed: [] },
  advisor_run: {
    name: "advisor-strategy-001",
    role: "strategy",
    request: 3,
    t_start: 2,
    t_end: null,
    returncode: null,
    denials: null,
    result: null,
  },
  advisor_step: {
    name: "advisor-strategy-001",
    t: 2.5,
    kind: "tool_use",
    tool: "Read",
    summary: "notes/strategy.md",
  },
  stats: { t_end: 12.5, counts: { requests: 4, frames: 30, pad: 6 }, decide_s_median: 0.4 },
  end: { record_end: true },
};

describe("parseLiveEvent", () => {
  it.each(LIVE_EVENT_KINDS)("%s の kind と、JSON として読んだ data を返す", (kind) => {
    const data = dataByKind[kind];
    expect(parseLiveEvent(kind, JSON.stringify(data))).toEqual({ kind, data });
  });

  it("知らない kind は null", () => {
    expect(parseLiveEvent("ping", JSON.stringify({ a: 1 }))).toBeNull();
    expect(parseLiveEvent("message", JSON.stringify({ a: 1 }))).toBeNull();
    expect(parseLiveEvent("", JSON.stringify({ a: 1 }))).toBeNull();
  });

  it("JSON として読めない data は null", () => {
    expect(parseLiveEvent("request", "{")).toBeNull();
    expect(parseLiveEvent("stats", "")).toBeNull();
    expect(parseLiveEvent("end", "NaN")).toBeNull();
  });

  it.each([
    ["null", "null"],
    ["配列", "[1, 2]"],
    ["数", "3"],
    ["文字列", '"end"'],
    ["真偽値", "true"],
  ])("data が object でない (%s) なら null", (_label, data) => {
    expect(parseLiveEvent("end", data)).toBeNull();
  });
});
