// lineDiff のテスト。LCS が 1 通りの入力の具体的な差分と、元の行に戻る・same の数が LCS の長さ・
// del が add より前、の性質を確かめる
import { describe, expect, it } from "vitest";
import { type DiffLine, diffLines } from "./lineDiff.ts";

// 最長共通部分列の長さ (テストの期待値を求めるための素朴な動的計画法)
const lcsLength = (left: readonly string[], right: readonly string[]): number => {
  let previous: number[] = new Array<number>(right.length + 1).fill(0);
  for (const leftLine of left) {
    const current: number[] = [0];
    right.forEach((rightLine, index) => {
      const diagonal = previous[index] ?? 0;
      const above = previous[index + 1] ?? 0;
      const beside = current[index] ?? 0;
      current.push(leftLine === rightLine ? diagonal + 1 : Math.max(above, beside));
    });
    previous = current;
  }
  return previous[right.length] ?? 0;
};

const textsOf = (lines: readonly DiffLine[], kinds: readonly DiffLine["kind"][]): string[] =>
  lines.filter((line) => kinds.includes(line.kind)).map((line) => line.text);

// 連続する same でない行のまとまりの中で、del がすべて add より前にあるか
const delsBeforeAdds = (lines: readonly DiffLine[]): boolean => {
  let seenAdd = false;
  for (const line of lines) {
    if (line.kind === "same") {
      seenAdd = false;
    } else if (line.kind === "add") {
      seenAdd = true;
    } else if (seenAdd) {
      return false;
    }
  }
  return true;
};

// 性質を確かめる入力の組。LCS の取り方が複数ある入力と、末尾の改行を含む入力を含む
const PROPERTY_CASES: readonly [string, string][] = [
  ["a\nb", "b\na"],
  ["a\nb\na", "a"],
  ["x\ny\nz\nx\ny", "y\nx\nz\ny\nx"],
  ["controls\n- A で決定\n- B で戻る\n", "controls\n- A で決定\n- X でメニュー\n- B で戻る\n"],
  ["1\n2\n3\n4\n5\n6", "6\n5\n4\n3\n2\n1"],
  ["same\nold1\nold2\nsame\nold3", "new1\nsame\nsame\nnew2\nnew3"],
];

describe("diffLines", () => {
  it("同じ文はすべて same にする", () => {
    expect(diffLines("a\nb\nc", "a\nb\nc")).toEqual([
      { kind: "same", text: "a" },
      { kind: "same", text: "b" },
      { kind: "same", text: "c" },
    ]);
  });

  it("末尾に足した行を add にする", () => {
    expect(diffLines("a\nb", "a\nb\nc")).toEqual([
      { kind: "same", text: "a" },
      { kind: "same", text: "b" },
      { kind: "add", text: "c" },
    ]);
  });

  it("消した行を del にする", () => {
    expect(diffLines("a\nb\nc", "a\nc")).toEqual([
      { kind: "same", text: "a" },
      { kind: "del", text: "b" },
      { kind: "same", text: "c" },
    ]);
  });

  it("書き換えた行は del を add より前に並べる", () => {
    expect(diffLines("a\nb\nc", "a\nx\nc")).toEqual([
      { kind: "same", text: "a" },
      { kind: "del", text: "b" },
      { kind: "add", text: "x" },
      { kind: "same", text: "c" },
    ]);
  });

  it("複数の行の書き換えも、まとまりの中で del をすべて add より前に並べる", () => {
    expect(diffLines("a\nb\nc\nd", "a\nx\ny\nd")).toEqual([
      { kind: "same", text: "a" },
      { kind: "del", text: "b" },
      { kind: "del", text: "c" },
      { kind: "add", text: "x" },
      { kind: "add", text: "y" },
      { kind: "same", text: "d" },
    ]);
  });

  it.each(PROPERTY_CASES)("same と del を並べると before の行に戻る (%j → %j)", (before, after) => {
    expect(textsOf(diffLines(before, after), ["same", "del"])).toEqual(before.split("\n"));
  });

  it.each(PROPERTY_CASES)("same と add を並べると after の行に戻る (%j → %j)", (before, after) => {
    expect(textsOf(diffLines(before, after), ["same", "add"])).toEqual(after.split("\n"));
  });

  it.each(PROPERTY_CASES)("same の行の数が LCS の長さに等しい (%j → %j)", (before, after) => {
    const sameCount = diffLines(before, after).filter((line) => line.kind === "same").length;
    expect(sameCount).toBe(lcsLength(before.split("\n"), after.split("\n")));
  });

  it.each(PROPERTY_CASES)("変わった箇所では del が add より前にある (%j → %j)", (before, after) => {
    expect(delsBeforeAdds(diffLines(before, after))).toBe(true);
  });
});
