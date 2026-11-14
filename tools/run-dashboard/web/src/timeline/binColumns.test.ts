// binColumns のテスト。列の px の位置 (端の列の切り詰めと整数へのそろえ方) と、カーソルの位置から列を引くことを確かめる
import { describe, expect, it } from "vitest";
import { binColumnRects, binIndexAt } from "./binColumns.ts";
import { binIndexRange } from "./halfSecondBins.ts";

/** binIndexRange の列の start を作る */
const cellsOf = (range: { from: number; to: number }): { start: number }[] => {
  const { first, last } = binIndexRange(range);
  return Array.from({ length: last - first + 1 }, (_, offset) => ({
    start: (first + offset) * 0.5,
  }));
};

describe("binColumnRects", () => {
  it("区間が列の境目にそろっていれば、列を等しい幅に並べる", () => {
    const range = { from: 0, to: 2 };
    expect(binColumnRects(cellsOf(range), range, 400)).toEqual([
      { x: 0, width: 100 },
      { x: 100, width: 100 },
      { x: 200, width: 100 },
      { x: 300, width: 100 },
    ]);
  });

  it("区間の端にかかる列は、区間の中の部分だけにする", () => {
    const range = { from: 0.25, to: 1.25 };
    // 列は 0〜0.5・0.5〜1・1〜1.5。px は 1 秒 = 100px
    expect(binColumnRects(cellsOf(range), range, 100)).toEqual([
      { x: 0, width: 25 },
      { x: 25, width: 50 },
      { x: 75, width: 25 },
    ]);
  });

  it("左端を切り捨て、右端を切り上げて、隣の列との間に隙間を作らない", () => {
    const range = { from: 0, to: 1.5 };
    const rects = binColumnRects(cellsOf(range), range, 100);
    expect(rects).toEqual([
      { x: 0, width: 34 },
      { x: 33, width: 34 },
      { x: 66, width: 34 },
    ]);
  });

  it("区間の幅が 0 以下なら、すべて幅 0 にする", () => {
    expect(binColumnRects([{ start: 0 }], { from: 1, to: 1 }, 100)).toEqual([{ x: 0, width: 0 }]);
  });
});

describe("binIndexAt", () => {
  const range = { from: 0.25, to: 1.25 };
  const cells = cellsOf(range);

  it("比率の位置の時刻が入る列を返す", () => {
    expect(binIndexAt(cells, range, 0)).toBe(0);
    expect(binIndexAt(cells, range, 0.3)).toBe(1);
    expect(binIndexAt(cells, range, 0.8)).toBe(2);
  });

  it("右端は最後の列、範囲の外の比率は端の列に切り詰める", () => {
    expect(binIndexAt(cellsOf({ from: 0, to: 2 }), { from: 0, to: 2 }, 1)).toBe(3);
    expect(binIndexAt(cells, range, -0.5)).toBe(0);
    expect(binIndexAt(cells, range, 1.5)).toBe(2);
  });

  it("列が無いか、区間の幅が 0 以下なら -1 を返す", () => {
    expect(binIndexAt([], range, 0.5)).toBe(-1);
    expect(binIndexAt(cells, { from: 1, to: 1 }, 0.5)).toBe(-1);
  });
});
