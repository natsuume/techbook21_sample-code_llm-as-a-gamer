// playbackController のテスト。上の段に出す入力の選び方と、入力の文を確かめる
import { describe, expect, it } from "vitest";
import type { PadItem, RangeResponse, RequestItem } from "../api/apiTypes.ts";
import {
  controllerAtPlayback,
  controllerParts,
  describeController,
  type PlaybackControllerInput,
} from "./playbackController.ts";

// テスト用の要求。受信時刻は送信時刻の 0.25 秒後
const request = (n: number, tSent: number, action: string): RequestItem => ({
  n,
  t_sent: tSent,
  t_received: tSent + 0.25,
  result: "ok",
  action,
  advisor_requested: [],
  advisor_accepted: [],
  p: null,
  adopted_count: null,
});

const padRow = (t: number, source: PadItem["source"], state: string): PadItem => ({
  t,
  source,
  state,
  ok: true,
  error: null,
});

const range = <T>(items: T[], available = true): RangeResponse<T> => ({ available, items });

const input = (fields: Partial<PlaybackControllerInput>): PlaybackControllerInput => ({
  pad: range<PadItem>([], false),
  requests: range<RequestItem>([], false),
  neutralTimes: [],
  position: 0,
  nextRequestPosition: null,
  ...fields,
});

describe("controllerAtPlayback", () => {
  it("pad の記録があれば、再生位置の後で最初の apply の行の時刻の入力を返す", () => {
    const pad = range([
      padRow(1.3125, "apply", "A+B+UP+LS:0.4375,0"),
      padRow(1.4375, "tap_release", "A+UP+LS:0.4375,0"),
    ]);
    const snapshot = controllerAtPlayback(
      input({ pad, requests: range([request(1, 1, "押下 A / 短押し none")]), position: 1.25 }),
    );
    expect(snapshot?.source).toBe("pad");
    expect(snapshot?.buttons).toEqual([
      { name: "A", kind: "hold" },
      { name: "B", kind: "tap" },
      { name: "UP", kind: "hold" },
    ]);
    expect(snapshot?.sticks).toEqual({ LS: { x: 0.4375, y: 0 } });
  });

  it("次の要求より前に apply の行が無ければ、再生位置の入力を返す", () => {
    const pad = range([padRow(0.5, "apply", "X"), padRow(3, "apply", "Y")]);
    const snapshot = controllerAtPlayback(input({ pad, position: 1, nextRequestPosition: 2 }));
    expect(snapshot?.buttons).toEqual([{ name: "X", kind: "hold" }]);
  });

  it("pad の記録が無く要求の記録があれば、要求の操作から組み立てる", () => {
    const requests = range([request(1, 1, "押下 A / 短押し B")]);
    const snapshot = controllerAtPlayback(input({ requests, position: 1.25 }));
    expect(snapshot?.source).toBe("requests");
    expect(snapshot?.buttons).toEqual([
      { name: "A", kind: "hold" },
      { name: "B", kind: "tap" },
    ]);
  });

  it("中立に戻した時刻の後は、要求の押下を出さない", () => {
    const requests = range([request(1, 1, "押下 A / 短押し none")]);
    const snapshot = controllerAtPlayback(input({ requests, neutralTimes: [2], position: 3 }));
    expect(snapshot?.buttons).toEqual([]);
  });

  it("pad と要求のどちらの記録も無ければ null", () => {
    expect(controllerAtPlayback(input({ position: 1 }))).toBeNull();
  });
});

describe("controllerParts", () => {
  it("短押し・押下のボタンと、倒しているスティックに分ける", () => {
    expect(
      controllerParts({
        buttons: [
          { name: "A", kind: "hold" },
          { name: "B", kind: "tap" },
          { name: "UP", kind: "hold" },
        ],
        sticks: { LS: { x: 0, y: 0 }, RS: { x: 0, y: -1 } },
        source: "pad",
      }),
    ).toEqual({ tap: ["B"], hold: ["A", "UP"], sticks: ["RS"] });
  });
});

describe("describeController", () => {
  it("短押し・押下・スティックを並べた文を返す", () => {
    expect(
      describeController({
        buttons: [
          { name: "A", kind: "hold" },
          { name: "B", kind: "tap" },
          { name: "UP", kind: "hold" },
        ],
        sticks: { LS: { x: 0.4375, y: 0 }, RS: { x: 0, y: -1 } },
        source: "pad",
      }),
    ).toBe("短押し B · 押下 A, UP · スティック LS, RS");
  });

  it("何も押していなければ「なし」と「中立」にする", () => {
    expect(describeController({ buttons: [], sticks: {}, source: "requests" })).toBe(
      "短押し なし · 押下 なし · スティック 中立",
    );
  });
});
