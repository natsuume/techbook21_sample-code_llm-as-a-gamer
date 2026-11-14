// eventMarks のテスト。点の印、復旧待ちの区間の開き方と閉じ方、時刻の並べ直しを確かめる
import { describe, expect, it } from "vitest";
import { buildEventMarks } from "./eventMarks.ts";

const T_END = 100;

describe("buildEventMarks", () => {
  it("decision_error と capture_stopped を点の印にする", () => {
    const events = [
      { t: 1, event: "decision_error", error: "timeout" },
      { t: 2, event: "capture_stopped", error: "device lost" },
    ];
    expect(buildEventMarks(events, T_END)).toEqual([
      { kind: "decision_error", t: 1, error: "timeout" },
      { kind: "capture_stopped", t: 2, error: "device lost" },
    ]);
  });

  it("error が文字列でなければ null にする", () => {
    const events = [
      { t: 1, event: "decision_error", error: 42 },
      { t: 2, event: "capture_stopped" },
      { t: 3, event: "decision_error", error: null },
    ];
    expect(buildEventMarks(events, T_END)).toEqual([
      { kind: "decision_error", t: 1, error: null },
      { kind: "capture_stopped", t: 2, error: null },
      { kind: "decision_error", t: 3, error: null },
    ]);
  });

  it("ほかの event は入れない", () => {
    const events = [
      { t: 0, event: "start", game: "sample" },
      { t: 1, event: "advisor", role: "controls" },
      { t: 2, event: "record_end", dropped: 0 },
    ];
    expect(buildEventMarks(events, T_END)).toEqual([]);
  });

  it("bridge_wait で開いた区間を、その後の最初の bridge_wait_end で閉じる", () => {
    const events = [
      { t: 5, event: "bridge_wait" },
      { t: 8, event: "bridge_wait_end", waited_s: 3.1 },
    ];
    expect(buildEventMarks(events, T_END)).toEqual([
      { kind: "bridge_wait", start: 5, end: 8, open: false, waitedS: 3.1 },
    ]);
  });

  it("閉じる行の waited_s が数でなければ waitedS を null にする", () => {
    const events = [
      { t: 5, event: "bridge_wait" },
      { t: 8, event: "bridge_wait_end", waited_s: "3" },
      { t: 10, event: "bridge_wait" },
      { t: 12, event: "bridge_wait_end" },
    ];
    expect(buildEventMarks(events, T_END)).toEqual([
      { kind: "bridge_wait", start: 5, end: 8, open: false, waitedS: null },
      { kind: "bridge_wait", start: 10, end: 12, open: false, waitedS: null },
    ]);
  });

  it("閉じる行が無ければ、tEnd まで開いた区間にする", () => {
    expect(buildEventMarks([{ t: 5, event: "bridge_wait" }], T_END)).toEqual([
      { kind: "bridge_wait", start: 5, end: T_END, open: true, waitedS: null },
    ]);
  });

  it("開いている間の bridge_wait は無視する", () => {
    const events = [
      { t: 5, event: "bridge_wait" },
      { t: 6, event: "bridge_wait" },
      { t: 8, event: "bridge_wait_end", waited_s: 3 },
    ];
    expect(buildEventMarks(events, T_END)).toEqual([
      { kind: "bridge_wait", start: 5, end: 8, open: false, waitedS: 3 },
    ]);
  });

  it("開いていない bridge_wait_end は無視する", () => {
    const events = [
      { t: 2, event: "bridge_wait_end", waited_s: 1 },
      { t: 5, event: "bridge_wait" },
      { t: 8, event: "bridge_wait_end", waited_s: 3 },
      { t: 9, event: "bridge_wait_end", waited_s: 4 },
    ];
    expect(buildEventMarks(events, T_END)).toEqual([
      { kind: "bridge_wait", start: 5, end: 8, open: false, waitedS: 3 },
    ]);
  });

  it("イベントを t の昇順に並べ直してから区間を作る", () => {
    const events = [
      { t: 8, event: "bridge_wait_end", waited_s: 3 },
      { t: 5, event: "bridge_wait" },
    ];
    expect(buildEventMarks(events, T_END)).toEqual([
      { kind: "bridge_wait", start: 5, end: 8, open: false, waitedS: 3 },
    ]);
  });

  it("同じ t のイベントは配列の順に処理する", () => {
    const endFirst = [
      { t: 5, event: "bridge_wait_end", waited_s: 0 },
      { t: 5, event: "bridge_wait" },
    ];
    expect(buildEventMarks(endFirst, T_END)).toEqual([
      { kind: "bridge_wait", start: 5, end: T_END, open: true, waitedS: null },
    ]);
  });

  it("負の t を含むイベントを並べ直し、印の時刻 (点は t、区間は start) の昇順で返す", () => {
    const events = [
      { t: 3, event: "decision_error", error: "timeout" },
      { t: 1, event: "bridge_wait" },
      { t: -2, event: "capture_stopped", error: "device lost" },
      { t: 2, event: "decision_error", error: "invalid" },
      { t: 5, event: "bridge_wait_end", waited_s: 4 },
    ];
    expect(buildEventMarks(events, T_END)).toEqual([
      { kind: "capture_stopped", t: -2, error: "device lost" },
      { kind: "bridge_wait", start: 1, end: 5, open: false, waitedS: 4 },
      { kind: "decision_error", t: 2, error: "invalid" },
      { kind: "decision_error", t: 3, error: "timeout" },
    ]);
  });
});
