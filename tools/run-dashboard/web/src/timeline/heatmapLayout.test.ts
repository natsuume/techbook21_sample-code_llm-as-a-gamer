// heatmapLayout のテスト。kind ごとのまとまりの順・行の高さ・まとまりの間の高さと、y から行を引くことを確かめる
import { describe, expect, it } from "vitest";
import type { QuestionKind } from "../api/apiTypes.ts";
import { heatmapLayout, heatmapRowAt } from "./heatmapLayout.ts";

const question = (name: string, kind: QuestionKind) => ({ name, kind });

/** 第 4 章の 37 の問いと同じ数の並び (button 17・stick_direction 4・stick_strength 12・advisor 4) */
const chapterQuestions = [
  ...Array.from({ length: 17 }, (_, index) => question(`button_${index}`, "button")),
  ...Array.from({ length: 4 }, (_, index) => question(`direction_${index}`, "stick_direction")),
  ...Array.from({ length: 12 }, (_, index) => question(`strength_${index}`, "stick_strength")),
  ...Array.from({ length: 4 }, (_, index) => question(`advisor_${index}`, "advisor")),
];

describe("heatmapLayout", () => {
  it("37 の問いを 4 つのまとまりにし、全体の高さを求める", () => {
    const layout = heatmapLayout(chapterQuestions);
    expect(layout.groups).toEqual([
      { kind: "button", label: "button 17", y: 0, height: 34 },
      { kind: "stick_direction", label: "stick_direction 4", y: 37, height: 8 },
      { kind: "stick_strength", label: "stick_strength 12", y: 48, height: 24 },
      { kind: "advisor", label: "advisor 4", y: 75, height: 32 },
    ]);
    expect(layout.height).toBe(107);
    expect(layout.rows).toHaveLength(37);
  });

  it("行は問いの位置を持ち、advisor の問いは高さ 8", () => {
    const layout = heatmapLayout(chapterQuestions);
    expect(layout.rows[0]).toEqual({
      questionIndex: 0,
      name: "button_0",
      kind: "button",
      y: 0,
      height: 2,
    });
    expect(layout.rows[33]).toEqual({
      questionIndex: 33,
      name: "advisor_0",
      kind: "advisor",
      y: 75,
      height: 8,
    });
  });

  it("問いの順が kind ごとでなくても、まとまりの順に並べ、questionIndex は元の位置を持つ", () => {
    const layout = heatmapLayout([
      question("advisor_a", "advisor"),
      question("button_a", "button"),
      question("button_b", "button"),
    ]);
    expect(layout.rows.map((row) => [row.name, row.questionIndex, row.y])).toEqual([
      ["button_a", 1, 0],
      ["button_b", 2, 2],
      ["advisor_a", 0, 7],
    ]);
    expect(layout.groups.map((group) => group.label)).toEqual(["button 2", "advisor 1"]);
  });

  it("問いが無ければ空で高さ 0", () => {
    expect(heatmapLayout([])).toEqual({ rows: [], groups: [], height: 0 });
  });
});

describe("heatmapRowAt", () => {
  const layout = heatmapLayout(chapterQuestions);

  it("y を含む行を返す", () => {
    expect(heatmapRowAt(layout, 0)?.name).toBe("button_0");
    expect(heatmapRowAt(layout, 3.5)?.name).toBe("button_1");
    expect(heatmapRowAt(layout, 82.9)?.name).toBe("advisor_0");
    expect(heatmapRowAt(layout, 83)?.name).toBe("advisor_1");
  });

  it("まとまりの間と、行の外は null を返す", () => {
    expect(heatmapRowAt(layout, 35)).toBeNull();
    expect(heatmapRowAt(layout, -1)).toBeNull();
    expect(heatmapRowAt(layout, 107)).toBeNull();
  });
});
