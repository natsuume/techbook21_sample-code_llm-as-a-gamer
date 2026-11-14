// operationText のテスト。第 4 章の書式の例の解釈と、解釈できない文を null にすることを確かめる
import { describe, expect, it } from "vitest";
import { parseOperation, parsePadState } from "./operationText.ts";

describe("parsePadState", () => {
  it("ボタンとスティックを解釈する", () => {
    expect(parsePadState("A+B+LS:0.8,0")).toEqual({
      buttons: ["A", "B"],
      sticks: { LS: { x: 0.8, y: 0 } },
    });
  });

  it("2 本のスティックを解釈する (負の値を含む)", () => {
    expect(parsePadState("LS:1,0+RS:0,-0.25")).toEqual({
      buttons: [],
      sticks: { LS: { x: 1, y: 0 }, RS: { x: 0, y: -0.25 } },
    });
  });

  it('"none" は何も押していない状態', () => {
    expect(parsePadState("none")).toEqual({ buttons: [], sticks: {} });
  });

  it("buttons は文の順のまま返す", () => {
    expect(parsePadState("UP+A")).toEqual({ buttons: ["UP", "A"], sticks: {} });
  });

  it("知らないボタン名を含む文は null", () => {
    expect(parsePadState("A+JUMP")).toBeNull();
  });

  it("スティックの値が数でない文は null", () => {
    expect(parsePadState("LS:abc,0")).toBeNull();
  });

  it("区切りが違う文は null", () => {
    expect(parsePadState("A,B")).toBeNull();
    expect(parsePadState("LS:0.8;0")).toBeNull();
  });
});

describe("parseOperation", () => {
  it("押下のボタンと短押しのボタンを解釈する", () => {
    expect(parseOperation("押下 A+UP / 短押し B")).toEqual({
      held: { buttons: ["A", "UP"], sticks: {} },
      taps: ["B"],
    });
  });

  it("押下のスティックを解釈する", () => {
    expect(parseOperation("押下 LS:-0.333333,0.275 / 短押し none")).toEqual({
      held: { buttons: [], sticks: { LS: { x: -0.333333, y: 0.275 } } },
      taps: [],
    });
  });

  it("何もしない操作を解釈する", () => {
    expect(parseOperation("押下 none / 短押し none")).toEqual({
      held: { buttons: [], sticks: {} },
      taps: [],
    });
  });

  it("buttons は文の順のまま返す", () => {
    expect(parseOperation("押下 UP+A / 短押し Y+B")).toEqual({
      held: { buttons: ["UP", "A"], sticks: {} },
      taps: ["Y", "B"],
    });
  });

  it("知らないボタン名を含む文は null", () => {
    expect(parseOperation("押下 JUMP / 短押し none")).toBeNull();
    expect(parseOperation("押下 none / 短押し JUMP")).toBeNull();
  });

  it("スティックの値が数でない文は null", () => {
    expect(parseOperation("押下 LS:abc,0 / 短押し none")).toBeNull();
  });

  it("区切りが違う文は null", () => {
    expect(parseOperation("押下 A, 短押し B")).toBeNull();
    expect(parseOperation("押下 A,B / 短押し none")).toBeNull();
  });

  it("短押しにスティックがある文は null", () => {
    expect(parseOperation("押下 A / 短押し LS:1,0")).toBeNull();
  });
});
