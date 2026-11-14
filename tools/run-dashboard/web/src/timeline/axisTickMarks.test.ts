// axisTickMarks のテスト。目盛りの位置・ラベル・寄せ方を確かめる
import { describe, expect, it } from "vitest";
import { axisTickMarks } from "./axisTickMarks.ts";

describe("axisTickMarks", () => {
  it("目盛りの時刻を、位置の比率と t= のラベルにする", () => {
    const marks = axisTickMarks({ from: 0, to: 10 });
    expect(marks.map((mark) => mark.t)).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect(marks[5]).toEqual({ t: 5, ratio: 0.5, label: "t=5", align: "center" });
  });

  it("両端の近くの目盛りは、ラベルをトラックの内側に寄せる", () => {
    const marks = axisTickMarks({ from: 0, to: 10 });
    expect(marks[0]?.align).toBe("start");
    expect(marks.at(-1)?.align).toBe("end");
  });

  it("ラベルの数は ja-JP の書式にする", () => {
    const marks = axisTickMarks({ from: 1200, to: 1230 });
    expect(marks.map((mark) => mark.label)).toEqual([
      "t=1,200",
      "t=1,205",
      "t=1,210",
      "t=1,215",
      "t=1,220",
      "t=1,225",
      "t=1,230",
    ]);
  });

  it("小数の目盛りは小数のままラベルにする", () => {
    const marks = axisTickMarks({ from: 0, to: 4 });
    expect(marks[1]?.label).toBe("t=0.5");
  });

  it("区間の端が有限の数でなければ空を返す", () => {
    expect(axisTickMarks({ from: 0, to: Number.POSITIVE_INFINITY })).toEqual([]);
  });
});
