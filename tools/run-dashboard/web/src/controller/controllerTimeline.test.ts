// controllerTimeline のテスト。pad の記録と要求の操作からの、時刻の入力と入力の区間の組み立て方を確かめる
import { describe, expect, it } from "vitest";
import type { EventItem, PadItem, PadSource, RequestItem } from "../api/apiTypes.ts";
import {
  type ControllerSnapshot,
  controllerAtFromPad,
  controllerAtFromRequests,
  inputSpansFromPad,
  inputSpansFromRequests,
  neutralTimesFromEvents,
} from "./controllerTimeline.ts";
import { BUTTON_NAMES } from "./operationText.ts";

// テスト用の pad の行。ok が false の行は error に文を入れる
const padRow = (t: number | null, source: PadSource, state: string, ok = true): PadItem => ({
  t,
  source,
  state,
  ok,
  error: ok ? null : "送れなかった",
});

// テスト用の要求。指定しないキーは、何も操作しなかった成功した要求の値にする
const request = (
  fields: Partial<RequestItem> & Pick<RequestItem, "n" | "t_sent" | "t_received">,
): RequestItem => ({
  result: "ok",
  action: "押下 none / 短押し none",
  advisor_requested: [],
  advisor_accepted: [],
  p: null,
  adopted_count: null,
  ...fields,
});

// snapshot のボタンの並びは規則に無いため、BUTTON_NAMES の順に並べ替えてから比べる
const sortButtons = (snapshot: ControllerSnapshot): ControllerSnapshot => ({
  ...snapshot,
  buttons: [...snapshot.buttons].sort(
    (left, right) => BUTTON_NAMES.indexOf(left.name) - BUTTON_NAMES.indexOf(right.name),
  ),
});

const WIDE_RANGE = { from: 0, to: 100 };

describe("controllerAtFromPad", () => {
  const pad = [
    padRow(1, "apply", "A"),
    padRow(2, "apply", "B+LS:0.5,0.5"),
    padRow(3, "neutral", "none"),
  ];

  it("行の状態は、その行の時刻から効く (始まりを含む)", () => {
    expect(sortButtons(controllerAtFromPad(pad, 1))).toEqual({
      buttons: [{ name: "A", kind: "hold" }],
      sticks: {},
      source: "pad",
    });
  });

  it("行の状態は、次の行の時刻の前まで効く (終わりを含まない)", () => {
    expect(sortButtons(controllerAtFromPad(pad, 1.99))).toEqual({
      buttons: [{ name: "A", kind: "hold" }],
      sticks: {},
      source: "pad",
    });
    expect(sortButtons(controllerAtFromPad(pad, 2))).toEqual({
      buttons: [{ name: "B", kind: "hold" }],
      sticks: { LS: { x: 0.5, y: 0.5 } },
      source: "pad",
    });
  });

  it("最初の行より前は何も押していない", () => {
    expect(controllerAtFromPad(pad, 0.5)).toEqual({ buttons: [], sticks: {}, source: "pad" });
  });

  it("最後の行の状態は、その行より後にも効く", () => {
    expect(sortButtons(controllerAtFromPad([padRow(1, "apply", "X+Y")], 1.5))).toEqual({
      buttons: [
        { name: "X", kind: "hold" },
        { name: "Y", kind: "hold" },
      ],
      sticks: {},
      source: "pad",
    });
  });

  it("ok が false の行は使わない", () => {
    const rows = [
      padRow(1, "apply", "A"),
      padRow(2, "apply", "B", false),
      padRow(3, "neutral", "none"),
    ];
    expect(sortButtons(controllerAtFromPad(rows, 2.5))).toEqual({
      buttons: [{ name: "A", kind: "hold" }],
      sticks: {},
      source: "pad",
    });
  });

  it("t が null の行は使わない", () => {
    const rows = [
      padRow(1, "apply", "A"),
      padRow(null, "apply", "B"),
      padRow(3, "neutral", "none"),
    ];
    expect(sortButtons(controllerAtFromPad(rows, 2.5))).toEqual({
      buttons: [{ name: "A", kind: "hold" }],
      sticks: {},
      source: "pad",
    });
  });

  it("状態を解釈できない行は使わない", () => {
    const rows = [
      padRow(1, "apply", "A"),
      padRow(2, "apply", "JUMP"),
      padRow(3, "neutral", "none"),
    ];
    expect(sortButtons(controllerAtFromPad(rows, 2.5))).toEqual({
      buttons: [{ name: "A", kind: "hold" }],
      sticks: {},
      source: "pad",
    });
  });

  it("行を t の昇順に並べてから使う", () => {
    const rows = [padRow(2, "apply", "B"), padRow(1, "apply", "A")];
    expect(sortButtons(controllerAtFromPad(rows, 1.5))).toEqual({
      buttons: [{ name: "A", kind: "hold" }],
      sticks: {},
      source: "pad",
    });
    expect(sortButtons(controllerAtFromPad(rows, 2.5))).toEqual({
      buttons: [{ name: "B", kind: "hold" }],
      sticks: {},
      source: "pad",
    });
  });

  it("同じ t の行は配列の順に並べる (後の行の状態が効く)", () => {
    const rows = [padRow(1, "apply", "A"), padRow(1, "apply", "B")];
    expect(sortButtons(controllerAtFromPad(rows, 1.5))).toEqual({
      buttons: [{ name: "B", kind: "hold" }],
      sticks: {},
      source: "pad",
    });
  });

  it("apply の行のボタンは、最初に消える行が tap_release なら tap、neutral なら hold にする", () => {
    const rows = [
      padRow(1, "apply", "A+B"),
      padRow(1.12, "tap_release", "A"),
      padRow(2, "neutral", "none"),
    ];
    expect(sortButtons(controllerAtFromPad(rows, 1.05))).toEqual({
      buttons: [
        { name: "A", kind: "hold" },
        { name: "B", kind: "tap" },
      ],
      sticks: {},
      source: "pad",
    });
  });

  it("tap_start の行のボタンも、最初に消える行が tap_release なら tap にする", () => {
    const rows = [
      padRow(1, "tap_start", "X+Y"),
      padRow(1.12, "tap_release", "Y"),
      padRow(2, "neutral", "none"),
    ];
    expect(sortButtons(controllerAtFromPad(rows, 1.05))).toEqual({
      buttons: [
        { name: "X", kind: "tap" },
        { name: "Y", kind: "hold" },
      ],
      sticks: {},
      source: "pad",
    });
  });

  it("最初に消える行が tap_release でないボタンと、最後まで消えないボタンは hold にする", () => {
    const rows = [padRow(1, "apply", "A+B"), padRow(1.12, "neutral", "A")];
    expect(sortButtons(controllerAtFromPad(rows, 1.05))).toEqual({
      buttons: [
        { name: "A", kind: "hold" },
        { name: "B", kind: "hold" },
      ],
      sticks: {},
      source: "pad",
    });
  });

  it("tap_release の行に残ったボタンも、後の tap_release の行で消えれば tap にする", () => {
    const rows = [
      padRow(1, "tap_release", "A"),
      padRow(1.12, "tap_release", "none"),
      padRow(2, "neutral", "none"),
    ];
    expect(sortButtons(controllerAtFromPad(rows, 1.05))).toEqual({
      buttons: [{ name: "A", kind: "tap" }],
      sticks: {},
      source: "pad",
    });
  });

  it("間に tap_start と別のボタンの tap_release を挟んでも、ボタンごとに消える行で tap を決める", () => {
    const rows = [
      padRow(1, "apply", "A+X"),
      padRow(1.1, "tap_start", "A+B+X"),
      padRow(1.12, "tap_release", "A+B"),
      padRow(1.22, "tap_release", "A"),
      padRow(2, "neutral", "none"),
    ];
    expect(sortButtons(controllerAtFromPad(rows, 1.05))).toEqual({
      buttons: [
        { name: "A", kind: "hold" },
        { name: "X", kind: "tap" },
      ],
      sticks: {},
      source: "pad",
    });
    expect(sortButtons(controllerAtFromPad(rows, 1.11))).toEqual({
      buttons: [
        { name: "A", kind: "hold" },
        { name: "B", kind: "tap" },
        { name: "X", kind: "tap" },
      ],
      sticks: {},
      source: "pad",
    });
    expect(sortButtons(controllerAtFromPad(rows, 1.15))).toEqual({
      buttons: [
        { name: "A", kind: "hold" },
        { name: "B", kind: "tap" },
      ],
      sticks: {},
      source: "pad",
    });
  });

  it("tap_release の行の後は、その行の状態のボタンを hold にする", () => {
    const rows = [
      padRow(1, "apply", "A+B"),
      padRow(1.12, "tap_release", "A"),
      padRow(2, "neutral", "none"),
    ];
    expect(sortButtons(controllerAtFromPad(rows, 1.5))).toEqual({
      buttons: [{ name: "A", kind: "hold" }],
      sticks: {},
      source: "pad",
    });
  });

  it("倒していないスティック ({ x: 0, y: 0 }) は入れない", () => {
    const rows = [padRow(1, "apply", "LS:0,0+RS:0.5,0"), padRow(2, "neutral", "none")];
    expect(controllerAtFromPad(rows, 1.5)).toEqual({
      buttons: [],
      sticks: { RS: { x: 0.5, y: 0 } },
      source: "pad",
    });
  });
});

describe("controllerAtFromRequests", () => {
  const requests = [
    request({ n: 1, t_sent: 0.5, t_received: 1, action: "押下 A+LS:1,0 / 短押し B" }),
    request({ n: 2, t_sent: 2.5, t_received: 3, action: "押下 none / 短押し none" }),
  ];

  it("要求の押下とスティックは、受信時刻から効く", () => {
    expect(sortButtons(controllerAtFromRequests(requests, [], 1.5))).toEqual({
      buttons: [{ name: "A", kind: "hold" }],
      sticks: { LS: { x: 1, y: 0 } },
      source: "requests",
    });
  });

  it("要求の押下とスティックは、次の要求の受信時刻の前まで効く", () => {
    expect(sortButtons(controllerAtFromRequests(requests, [], 2.99))).toEqual({
      buttons: [{ name: "A", kind: "hold" }],
      sticks: { LS: { x: 1, y: 0 } },
      source: "requests",
    });
    expect(controllerAtFromRequests(requests, [], 3)).toEqual({
      buttons: [],
      sticks: {},
      source: "requests",
    });
  });

  it("短押しは受信時刻から TAP_SECONDS の間だけ効く", () => {
    expect(sortButtons(controllerAtFromRequests(requests, [], 1.05))).toEqual({
      buttons: [
        { name: "A", kind: "hold" },
        { name: "B", kind: "tap" },
      ],
      sticks: { LS: { x: 1, y: 0 } },
      source: "requests",
    });
    expect(sortButtons(controllerAtFromRequests(requests, [], 1.2))).toEqual({
      buttons: [{ name: "A", kind: "hold" }],
      sticks: { LS: { x: 1, y: 0 } },
      source: "requests",
    });
  });

  it("最初の要求の受信時刻より前は何も押していない", () => {
    expect(controllerAtFromRequests(requests, [], 0.9)).toEqual({
      buttons: [],
      sticks: {},
      source: "requests",
    });
  });

  it("中立に戻す時刻から、押下とスティックを中立にする (始まりを含む)", () => {
    const withFailure = [
      request({ n: 1, t_sent: 0.5, t_received: 1, action: "押下 A+LS:1,0 / 短押し none" }),
      request({ n: 2, t_sent: 1.5, t_received: null, result: "error", action: null }),
      request({ n: 3, t_sent: 2.5, t_received: 3, action: "押下 X / 短押し none" }),
    ];
    expect(sortButtons(controllerAtFromRequests(withFailure, [2.1], 2.09))).toEqual({
      buttons: [{ name: "A", kind: "hold" }],
      sticks: { LS: { x: 1, y: 0 } },
      source: "requests",
    });
    expect(controllerAtFromRequests(withFailure, [2.1], 2.1)).toEqual({
      buttons: [],
      sticks: {},
      source: "requests",
    });
    expect(controllerAtFromRequests(withFailure, [2.1], 3)).toEqual({
      buttons: [{ name: "X", kind: "hold" }],
      sticks: {},
      source: "requests",
    });
  });

  it("ok でない要求は使わず、その時刻では中立に戻さない", () => {
    const withDiscarded = [
      request({ n: 1, t_sent: 0.5, t_received: 1, action: "押下 A / 短押し none" }),
      request({
        n: 2,
        t_sent: 1.8,
        t_received: 2,
        result: "discarded",
        action: "押下 B / 短押し none",
      }),
    ];
    expect(sortButtons(controllerAtFromRequests(withDiscarded, [], 2.5))).toEqual({
      buttons: [{ name: "A", kind: "hold" }],
      sticks: {},
      source: "requests",
    });
  });

  it("中立に戻す時刻が使う要求の受信時刻と同じなら、その要求の押下と短押しは効かない", () => {
    const sameTime = [
      request({ n: 1, t_sent: 0.5, t_received: 1, action: "押下 A / 短押し none" }),
      request({ n: 2, t_sent: 1.5, t_received: 2, action: "押下 X / 短押し B" }),
    ];
    expect(controllerAtFromRequests(sameTime, [2], 2)).toEqual({
      buttons: [],
      sticks: {},
      source: "requests",
    });
    expect(controllerAtFromRequests(sameTime, [2], 2.05)).toEqual({
      buttons: [],
      sticks: {},
      source: "requests",
    });
  });

  it("中立に戻す時刻で短押しも閉じる", () => {
    const tapThenNeutral = [
      request({ n: 1, t_sent: 0.5, t_received: 1, action: "押下 A / 短押し B" }),
    ];
    expect(sortButtons(controllerAtFromRequests(tapThenNeutral, [1.05], 1.04))).toEqual({
      buttons: [
        { name: "A", kind: "hold" },
        { name: "B", kind: "tap" },
      ],
      sticks: {},
      source: "requests",
    });
    expect(controllerAtFromRequests(tapThenNeutral, [1.05], 1.06)).toEqual({
      buttons: [],
      sticks: {},
      source: "requests",
    });
  });

  it("短押しは次の使う要求の受信時刻では閉じない", () => {
    const tapAcrossNext = [
      request({ n: 1, t_sent: 0.5, t_received: 1, action: "押下 none / 短押し B" }),
      request({ n: 2, t_sent: 0.9, t_received: 1.05, action: "押下 A / 短押し none" }),
    ];
    expect(sortButtons(controllerAtFromRequests(tapAcrossNext, [], 1.1))).toEqual({
      buttons: [
        { name: "A", kind: "hold" },
        { name: "B", kind: "tap" },
      ],
      sticks: {},
      source: "requests",
    });
  });

  it("t_received が null の要求は使わない", () => {
    const withoutReceived = [
      request({ n: 1, t_sent: 0.5, t_received: 1, action: "押下 A / 短押し none" }),
      request({ n: 2, t_sent: 1.5, t_received: null, action: "押下 B / 短押し none" }),
      request({ n: 3, t_sent: 2.5, t_received: 3, action: "押下 X / 短押し none" }),
    ];
    expect(sortButtons(controllerAtFromRequests(withoutReceived, [], 2))).toEqual({
      buttons: [{ name: "A", kind: "hold" }],
      sticks: {},
      source: "requests",
    });
  });

  it("操作の文を解釈できない要求は使わない", () => {
    const withUnknown = [
      request({ n: 1, t_sent: 0.5, t_received: 1, action: "押下 A / 短押し none" }),
      request({ n: 2, t_sent: 1.5, t_received: 2, action: "押下 JUMP / 短押し none" }),
      request({ n: 3, t_sent: 2.5, t_received: 3, action: "押下 X / 短押し none" }),
    ];
    expect(sortButtons(controllerAtFromRequests(withUnknown, [], 2.5))).toEqual({
      buttons: [{ name: "A", kind: "hold" }],
      sticks: {},
      source: "requests",
    });
  });

  it("押下と短押しの両方にあるボタンは短押しとして扱う", () => {
    const both = [
      request({ n: 1, t_sent: 0.5, t_received: 1, action: "押下 A / 短押し A" }),
      request({ n: 2, t_sent: 2.5, t_received: 3, action: "押下 none / 短押し none" }),
    ];
    expect(controllerAtFromRequests(both, [], 1.05)).toEqual({
      buttons: [{ name: "A", kind: "tap" }],
      sticks: {},
      source: "requests",
    });
  });
});

describe("inputSpansFromPad", () => {
  it("行のボタンの区間を返し、隣り合う同じボタン・同じ kind の区間をつなぐ", () => {
    const rows = [padRow(1, "apply", "A"), padRow(2, "apply", "A+B"), padRow(3, "neutral", "none")];
    expect(inputSpansFromPad(rows, WIDE_RANGE, 10)).toEqual({
      buttons: [
        { button: "A", kind: "hold", start: 1, end: 3 },
        { button: "B", kind: "hold", start: 2, end: 3 },
      ],
      sticks: [],
      source: "pad",
    });
  });

  it("離れた区間はつながない", () => {
    const rows = [
      padRow(1, "apply", "A"),
      padRow(2, "neutral", "none"),
      padRow(3, "apply", "A"),
      padRow(4, "neutral", "none"),
    ];
    expect(inputSpansFromPad(rows, WIDE_RANGE, 10)).toEqual({
      buttons: [
        { button: "A", kind: "hold", start: 1, end: 2 },
        { button: "A", kind: "hold", start: 3, end: 4 },
      ],
      sticks: [],
      source: "pad",
    });
  });

  it("最後の行の状態は tEnd まで効く", () => {
    expect(inputSpansFromPad([padRow(1, "apply", "X")], WIDE_RANGE, 8)).toEqual({
      buttons: [{ button: "X", kind: "hold", start: 1, end: 8 }],
      sticks: [],
      source: "pad",
    });
  });

  it("range と重なる区間を range に切り詰め、重ならない区間は入れない", () => {
    const rows = [
      padRow(1, "apply", "A"),
      padRow(5, "neutral", "none"),
      padRow(6, "apply", "B"),
      padRow(7, "neutral", "none"),
    ];
    expect(inputSpansFromPad(rows, { from: 2, to: 4 }, 10)).toEqual({
      buttons: [{ button: "A", kind: "hold", start: 2, end: 4 }],
      sticks: [],
      source: "pad",
    });
  });

  it("tap_release の前の行のボタンを tap と hold に分け、hold の区間は次の行の区間とつなぐ", () => {
    const rows = [
      padRow(1, "apply", "A+B"),
      padRow(1.12, "tap_release", "A"),
      padRow(2, "neutral", "none"),
    ];
    expect(inputSpansFromPad(rows, WIDE_RANGE, 10)).toEqual({
      buttons: [
        { button: "A", kind: "hold", start: 1, end: 2 },
        { button: "B", kind: "tap", start: 1, end: 1.12 },
      ],
      sticks: [],
      source: "pad",
    });
  });

  it("並びは start の昇順、同じ start はボタンの BUTTON_NAMES の順", () => {
    const rows = [
      padRow(1, "apply", "UP+B"),
      padRow(0.5, "apply", "ZR"),
      padRow(2, "apply", "A"),
      padRow(3, "neutral", "none"),
    ];
    expect(inputSpansFromPad(rows, WIDE_RANGE, 10)).toEqual({
      buttons: [
        { button: "ZR", kind: "hold", start: 0.5, end: 1 },
        { button: "B", kind: "hold", start: 1, end: 2 },
        { button: "UP", kind: "hold", start: 1, end: 2 },
        { button: "A", kind: "hold", start: 2, end: 3 },
      ],
      sticks: [],
      source: "pad",
    });
  });

  it("使わない行 (ok が false、t が null、状態を解釈できない) を除いて区間を作る", () => {
    const rows = [
      padRow(1, "apply", "A"),
      padRow(2, "apply", "B", false),
      padRow(null, "apply", "X"),
      padRow(3, "apply", "JUMP"),
      padRow(4, "neutral", "none"),
    ];
    expect(inputSpansFromPad(rows, WIDE_RANGE, 10)).toEqual({
      buttons: [{ button: "A", kind: "hold", start: 1, end: 4 }],
      sticks: [],
      source: "pad",
    });
  });

  it("スティックの区間は同じ magnitude のときだけつなぐ", () => {
    const rows = [
      padRow(1, "apply", "LS:1,0"),
      padRow(2, "apply", "LS:0,1"),
      padRow(3, "apply", "LS:0.3,0.4"),
      padRow(4, "neutral", "none"),
    ];
    expect(inputSpansFromPad(rows, WIDE_RANGE, 10)).toEqual({
      buttons: [],
      sticks: [
        { stick: "LS", start: 1, end: 3, magnitude: expect.closeTo(1) },
        { stick: "LS", start: 3, end: 4, magnitude: expect.closeTo(0.5) },
      ],
      source: "pad",
    });
  });

  it("magnitude は 1 を上限にする", () => {
    const rows = [padRow(1, "apply", "LS:1,1"), padRow(2, "neutral", "none")];
    expect(inputSpansFromPad(rows, WIDE_RANGE, 10)).toEqual({
      buttons: [],
      sticks: [{ stick: "LS", start: 1, end: 2, magnitude: expect.closeTo(1) }],
      source: "pad",
    });
  });

  it("倒していないスティック ({ x: 0, y: 0 }) の区間は入れない", () => {
    const rows = [padRow(1, "apply", "LS:0,0+RS:0.6,0.8"), padRow(2, "neutral", "none")];
    expect(inputSpansFromPad(rows, WIDE_RANGE, 10)).toEqual({
      buttons: [],
      sticks: [{ stick: "RS", start: 1, end: 2, magnitude: expect.closeTo(1) }],
      source: "pad",
    });
  });
});

describe("inputSpansFromRequests", () => {
  it("押下は次の要求の受信時刻まで、短押しは TAP_SECONDS の間の区間にする", () => {
    const requests = [
      request({ n: 1, t_sent: 0.5, t_received: 1, action: "押下 A+LS:1,0 / 短押し B" }),
      request({ n: 2, t_sent: 1.5, t_received: 2, action: "押下 A / 短押し none" }),
      request({ n: 3, t_sent: 2.5, t_received: 3, action: "押下 none / 短押し none" }),
    ];
    expect(inputSpansFromRequests(requests, [], WIDE_RANGE, 5)).toEqual({
      buttons: [
        { button: "A", kind: "hold", start: 1, end: 3 },
        { button: "B", kind: "tap", start: 1, end: expect.closeTo(1.12) },
      ],
      sticks: [{ stick: "LS", start: 1, end: 2, magnitude: expect.closeTo(1) }],
      source: "requests",
    });
  });

  it("使わない要求を飛ばして押下を続け、中立に戻す時刻で押下とスティックを閉じる", () => {
    const requests = [
      request({ n: 1, t_sent: 0.5, t_received: 1, action: "押下 X+LS:1,0 / 短押し none" }),
      request({ n: 2, t_sent: 1.5, t_received: 2, action: "押下 JUMP / 短押し none" }),
      request({ n: 3, t_sent: 2.2, t_received: null, result: "error", action: null }),
      request({ n: 4, t_sent: 2.5, t_received: 3, action: "押下 Y / 短押し none" }),
    ];
    expect(inputSpansFromRequests(requests, [2.4], WIDE_RANGE, 5)).toEqual({
      buttons: [
        { button: "X", kind: "hold", start: 1, end: 2.4 },
        { button: "Y", kind: "hold", start: 3, end: 5 },
      ],
      sticks: [{ stick: "LS", start: 1, end: 2.4, magnitude: expect.closeTo(1) }],
      source: "requests",
    });
  });

  it("最後の使う要求の押下は、その後の中立に戻す時刻で閉じる", () => {
    const requests = [
      request({ n: 1, t_sent: 0.5, t_received: 1, action: "押下 Y / 短押し none" }),
    ];
    expect(inputSpansFromRequests(requests, [0.5, 2], WIDE_RANGE, 4)).toEqual({
      buttons: [{ button: "Y", kind: "hold", start: 1, end: 2 }],
      sticks: [],
      source: "requests",
    });
  });

  it("最後の使う要求の押下は tEnd まで効く", () => {
    const requests = [
      request({ n: 1, t_sent: 0.5, t_received: 1, action: "押下 Y / 短押し none" }),
    ];
    expect(inputSpansFromRequests(requests, [], WIDE_RANGE, 4)).toEqual({
      buttons: [{ button: "Y", kind: "hold", start: 1, end: 4 }],
      sticks: [],
      source: "requests",
    });
  });

  it("range と重なる区間を range に切り詰める", () => {
    const requests = [
      request({ n: 1, t_sent: 0.5, t_received: 1, action: "押下 A / 短押し none" }),
      request({ n: 2, t_sent: 5.5, t_received: 6, action: "押下 none / 短押し none" }),
    ];
    expect(inputSpansFromRequests(requests, [], { from: 2, to: 4 }, 10)).toEqual({
      buttons: [{ button: "A", kind: "hold", start: 2, end: 4 }],
      sticks: [],
      source: "requests",
    });
  });

  it("隣り合っていても kind の違う区間はつながない", () => {
    const requests = [
      request({ n: 1, t_sent: -0.5, t_received: 0, action: "押下 none / 短押し B" }),
      request({ n: 2, t_sent: 0.1, t_received: 0.12, action: "押下 B / 短押し none" }),
    ];
    expect(inputSpansFromRequests(requests, [], WIDE_RANGE, 1)).toEqual({
      buttons: [
        { button: "B", kind: "tap", start: 0, end: expect.closeTo(0.12) },
        { button: "B", kind: "hold", start: 0.12, end: 1 },
      ],
      sticks: [],
      source: "requests",
    });
  });

  it("スティックの区間は同じ magnitude のときだけつなぐ", () => {
    const requests = [
      request({ n: 1, t_sent: 0.5, t_received: 1, action: "押下 LS:1,0 / 短押し none" }),
      request({ n: 2, t_sent: 1.5, t_received: 2, action: "押下 LS:0,-1 / 短押し none" }),
      request({ n: 3, t_sent: 2.5, t_received: 3, action: "押下 LS:0.5,0 / 短押し none" }),
    ];
    expect(inputSpansFromRequests(requests, [], WIDE_RANGE, 4)).toEqual({
      buttons: [],
      sticks: [
        { stick: "LS", start: 1, end: 3, magnitude: expect.closeTo(1) },
        { stick: "LS", start: 3, end: 4, magnitude: expect.closeTo(0.5) },
      ],
      source: "requests",
    });
  });
});

describe("neutralTimesFromEvents", () => {
  const event = (t: number, name: string): EventItem => ({ t, event: name });

  it("判断の失敗・キャプチャーの停止・接続の復旧待ち・割り込み・接続の失敗の時刻を返す", () => {
    const events = [
      event(1, "decision_error"),
      event(2, "capture_stopped"),
      event(3, "bridge_wait"),
      event(4, "pipeline_error"),
      event(5, "interrupted"),
    ];
    expect(neutralTimesFromEvents(events)).toEqual([1, 2, 3, 4, 5]);
  });

  it("ほかの出来事は入れない", () => {
    const events = [
      event(0, "start"),
      event(1, "bridge_wait_end"),
      event(2, "send_error"),
      event(3, "advisor"),
    ];
    expect(neutralTimesFromEvents(events)).toEqual([]);
  });

  it("時刻の昇順に並べる", () => {
    const events = [
      event(5, "bridge_wait"),
      event(-0.5, "decision_error"),
      event(2, "capture_stopped"),
    ];
    expect(neutralTimesFromEvents(events)).toEqual([-0.5, 2, 5]);
  });
});
