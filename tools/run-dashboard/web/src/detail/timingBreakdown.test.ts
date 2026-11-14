// timingBreakdown のテスト。所要時間の 4 区間の積み上げ棒の位置と、ms の書式を確かめる
import { describe, expect, it } from "vitest";
import { formatMilliseconds, timingBreakdown } from "./timingBreakdown.ts";

describe("timingBreakdown", () => {
  it("4 区間の秒と、合計に対する位置を返す", () => {
    const breakdown = timingBreakdown({
      select_s: 0,
      encode_s: 0.125,
      decide_s: 0.25,
      apply_s: 0.125,
    });
    expect(breakdown.totalSeconds).toBe(0.5);
    expect(breakdown.segments).toEqual([
      { key: "select_s", label: "選択", seconds: 0, start: 0, width: 0 },
      { key: "encode_s", label: "符号化", seconds: 0.125, start: 0, width: 0.25 },
      { key: "decide_s", label: "判断 (HTTP を含む)", seconds: 0.25, start: 0.25, width: 0.5 },
      { key: "apply_s", label: "操作の送信", seconds: 0.125, start: 0.75, width: 0.25 },
    ]);
  });

  it("値の無い区間は幅 0 にし、合計に入れない", () => {
    const breakdown = timingBreakdown({
      select_s: null,
      encode_s: 0.25,
      decide_s: 0.75,
      apply_s: null,
    });
    expect(breakdown.totalSeconds).toBe(1);
    expect(breakdown.segments.map((segment) => segment.width)).toEqual([0, 0.25, 0.75, 0]);
  });

  it("どの区間も値が無ければ、合計は null で幅はすべて 0", () => {
    const breakdown = timingBreakdown({
      select_s: null,
      encode_s: null,
      decide_s: null,
      apply_s: null,
    });
    expect(breakdown.totalSeconds).toBeNull();
    expect(breakdown.segments.every((segment) => segment.width === 0)).toBe(true);
  });

  it("合計が 0 なら幅はすべて 0", () => {
    const breakdown = timingBreakdown({ select_s: 0, encode_s: 0, decide_s: 0, apply_s: 0 });
    expect(breakdown.totalSeconds).toBe(0);
    expect(breakdown.segments.every((segment) => segment.width === 0)).toBe(true);
  });
});

describe("formatMilliseconds", () => {
  it("秒をミリ秒の小数 2 桁にする", () => {
    expect(formatMilliseconds(0.03125)).toBe("31.25 ms");
    expect(formatMilliseconds(1.5)).toBe("1,500.00 ms");
  });

  it("null は —", () => {
    expect(formatMilliseconds(null)).toBe("—");
  });
});
