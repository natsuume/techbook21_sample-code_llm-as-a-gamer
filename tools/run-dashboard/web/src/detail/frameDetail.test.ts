// frameDetail のテスト。開くフレームの位置、前後の画像の位置、判定の状態の文、フレームを載せた要求を確かめる
import { describe, expect, it } from "vitest";
import type { RequestDetail, RequestImage } from "../api/apiTypes.ts";
import {
  formatDecimal,
  framePhaseText,
  neighborFrameSlots,
  requestsShowingFrame,
  resolveFrameIndex,
  valleyDistanceLabel,
} from "./frameDetail.ts";

describe("resolveFrameIndex", () => {
  const times = [0.5, 1, 1.25, 5.5];

  it("選んだフレームがあれば、その位置を範囲に切り詰めて返す", () => {
    expect(resolveFrameIndex(2, times, 0)).toBe(2);
    expect(resolveFrameIndex(9, times, 0)).toBe(3);
    expect(resolveFrameIndex(-1, times, 0)).toBe(0);
  });

  it("選んでいなければ、再生位置以前の最後のフレーム", () => {
    expect(resolveFrameIndex(null, times, 1.1)).toBe(1);
    expect(resolveFrameIndex(null, times, 99)).toBe(3);
  });

  it("再生位置以前のフレームが無ければ最初のフレーム", () => {
    expect(resolveFrameIndex(null, times, 0)).toBe(0);
  });

  it("フレームが無ければ null", () => {
    expect(resolveFrameIndex(null, [], 1)).toBeNull();
    expect(resolveFrameIndex(0, [], 1)).toBeNull();
  });
});

describe("neighborFrameSlots", () => {
  it("前後 radius 枚の枠を返し、範囲の外の枠は index を null にする", () => {
    expect(neighborFrameSlots(1, 4, 3).map((slot) => slot.index)).toEqual([
      null,
      null,
      0,
      1,
      2,
      3,
      null,
    ]);
    expect(neighborFrameSlots(5, 10, 3).map((slot) => slot.index)).toEqual([2, 3, 4, 5, 6, 7, 8]);
  });

  it("offset は -radius から radius", () => {
    expect(neighborFrameSlots(0, 1, 1)).toEqual([
      { offset: -1, index: null },
      { offset: 0, index: 0 },
      { offset: 1, index: null },
    ]);
  });
});

describe("formatDecimal", () => {
  it("小数 digits 桁の ja-JP の書式にし、null は —", () => {
    expect(formatDecimal(4512.345, 1)).toBe("4,512.3");
    expect(formatDecimal(0.5, 2)).toBe("0.50");
    expect(formatDecimal(null, 1)).toBe("—");
  });
});

describe("framePhaseText・valleyDistanceLabel", () => {
  it("rising は谷に入る前、falling は谷の中", () => {
    expect(framePhaseText("rising")).toBe("谷に入る前 (rising)");
    expect(framePhaseText("falling")).toBe("谷の中 (falling)");
    expect(valleyDistanceLabel({ phase: "rising" })).toBe("谷に入るまで");
    expect(valleyDistanceLabel({ phase: "falling" })).toBe("谷を確定するまで");
  });
});

describe("requestsShowingFrame", () => {
  const image = (thumb: string | null): RequestImage => ({
    label: "最新",
    t: 1,
    bytes: 641,
    thumb,
    file: null,
  });
  const detail = (n: number, thumbs: (string | null)[]): RequestDetail => ({
    n,
    t_sent: n,
    t_received: n + 0.25,
    result: "ok",
    action: null,
    advisor_requested: [],
    advisor_accepted: [],
    p: null,
    adopted_count: null,
    images: thumbs.map(image),
    timings: { select_s: null, encode_s: null, decide_s: null, apply_s: null },
    payload_bytes: null,
    history_text: "",
    previous_operation_text: null,
    note_version: null,
    probabilities: null,
    choices: null,
    advisor_probabilities: null,
    adopted: null,
  });

  it("縮小画像が一致する画像を載せた要求の n を、配列の順に返す", () => {
    expect(
      requestsShowingFrame(
        [
          detail(1, ["thumbs/a.jpg"]),
          undefined,
          detail(3, [null, "thumbs/b.jpg"]),
          detail(4, ["thumbs/b.jpg"]),
          { available: false, reason: "old_format" },
        ],
        "thumbs/b.jpg",
      ),
    ).toEqual([3, 4]);
  });

  it("一致が無ければ []", () => {
    expect(requestsShowingFrame([detail(1, ["thumbs/a.jpg"])], "thumbs/z.jpg")).toEqual([]);
  });
});
