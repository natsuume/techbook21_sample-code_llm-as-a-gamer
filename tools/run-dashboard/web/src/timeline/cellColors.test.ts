// cellColors のテスト。hex の読み方、色の補間、帯の色の種類、ヒートマップのセルの色を確かめる
import { describe, expect, it } from "vitest";
import {
  adoptedCountTone,
  type HeatPalette,
  interpolateColor,
  parseHexColor,
  questionCellColor,
} from "./cellColors.ts";

describe("parseHexColor", () => {
  it("6 桁の hex を赤・緑・青にする (前後の空白と大文字を許す)", () => {
    expect(parseHexColor(" #2C6bd1 ")).toEqual({ r: 0x2c, g: 0x6b, b: 0xd1 });
  });

  it("ほかの書式は null を返す", () => {
    expect(parseHexColor("#fff")).toBeNull();
    expect(parseHexColor("rgb(1, 2, 3)")).toBeNull();
    expect(parseHexColor("")).toBeNull();
  });
});

describe("interpolateColor", () => {
  const low = { r: 0, g: 100, b: 200 };
  const high = { r: 100, g: 200, b: 0 };

  it("両端は low と high の色になる", () => {
    expect(interpolateColor(low, high, 0)).toBe("rgb(0, 100, 200)");
    expect(interpolateColor(low, high, 1)).toBe("rgb(100, 200, 0)");
  });

  it("途中は線形に補間して四捨五入する", () => {
    expect(interpolateColor(low, high, 0.255)).toBe("rgb(26, 126, 149)");
  });

  it("0〜1 の外の値と数でない値は切り詰める", () => {
    expect(interpolateColor(low, high, -1)).toBe("rgb(0, 100, 200)");
    expect(interpolateColor(low, high, 2)).toBe("rgb(100, 200, 0)");
    expect(interpolateColor(low, high, Number.NaN)).toBe("rgb(0, 100, 200)");
  });
});

describe("adoptedCountTone", () => {
  it("null は null、0 は zero、1 は one、2 以上は many", () => {
    expect(adoptedCountTone(null)).toBeNull();
    expect(adoptedCountTone(0)).toBe("zero");
    expect(adoptedCountTone(1)).toBe("one");
    expect(adoptedCountTone(2)).toBe("many");
    expect(adoptedCountTone(7)).toBe("many");
  });
});

describe("questionCellColor", () => {
  const palette: HeatPalette = {
    low: { r: 0, g: 0, b: 0 },
    high: { r: 200, g: 200, b: 200 },
    hot: "#e8930c",
  };

  it("advisor の問いでしきい値を超える値は hot にする", () => {
    expect(questionCellColor(0.61, "advisor", 0.6, palette)).toBe("#e8930c");
  });

  it("advisor の問いでしきい値ちょうどの値は補間の色にする", () => {
    expect(questionCellColor(0.6, "advisor", 0.6, palette)).toBe("rgb(120, 120, 120)");
  });

  it("advisor 以外の問いは、しきい値を超えても補間の色にする", () => {
    expect(questionCellColor(0.9, "button", 0.6, palette)).toBe("rgb(180, 180, 180)");
  });
});
