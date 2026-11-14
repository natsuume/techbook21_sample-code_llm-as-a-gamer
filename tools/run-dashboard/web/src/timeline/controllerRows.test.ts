// controllerRows のテスト。5 行の並びと、入力の区間の振り分けを確かめる
import { describe, expect, it } from "vitest";
import { BUTTON_NAMES } from "../controller/operationText.ts";
import { CONTROLLER_ROWS, splitInputSpans } from "./controllerRows.ts";

describe("CONTROLLER_ROWS", () => {
  it("5 行の名前の順", () => {
    expect(CONTROLLER_ROWS.map((row) => row.label)).toEqual([
      "十字キー",
      "A B X Y",
      "L R ZL ZR",
      "+ − HOME",
      "スティック",
    ]);
  });

  it("すべてのボタンを、どれか 1 行だけに入れる", () => {
    const placed = CONTROLLER_ROWS.flatMap((row) => row.buttons);
    expect([...placed].sort()).toEqual([...BUTTON_NAMES].sort());
  });
});

describe("splitInputSpans", () => {
  it("ボタンとスティックの区間を行に振り分け、行の中は渡した順のまま", () => {
    const rows = splitInputSpans({
      source: "pad",
      buttons: [
        { button: "A", kind: "hold", start: 0, end: 1 },
        { button: "UP", kind: "tap", start: 0.5, end: 0.62 },
        { button: "B", kind: "tap", start: 2, end: 2.12 },
        { button: "R3", kind: "hold", start: 3, end: 4 },
      ],
      sticks: [{ stick: "RS", start: 1, end: 2, magnitude: 0.5 }],
    });
    expect(rows[0]?.buttons.map((span) => span.button)).toEqual(["UP"]);
    expect(rows[1]?.buttons.map((span) => span.button)).toEqual(["A", "B"]);
    expect(rows[2]?.buttons).toEqual([]);
    expect(rows[4]?.buttons.map((span) => span.button)).toEqual(["R3"]);
    expect(rows[4]?.sticks.map((span) => span.stick)).toEqual(["RS"]);
    expect(rows[0]?.sticks).toEqual([]);
  });
});
