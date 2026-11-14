// frameChart のテスト。縦の上限、座標、null での線の切れ方、最も近いフレームの引き方を確かめる
import { describe, expect, it } from "vitest";
import {
  chartX,
  chartY,
  diffScaleMax,
  nearestIndexWithin,
  polylineSegments,
  sharpnessScaleMax,
} from "./frameChart.ts";

describe("sharpnessScaleMax", () => {
  it("鮮明度と谷とみなす値の最大値 × 1.08 を返す", () => {
    expect(
      sharpnessScaleMax([
        { sharpness: 10, ref: 50 },
        { sharpness: 100, ref: 20 },
      ]),
    ).toBeCloseTo(108);
  });

  it("フレームが無いか最大値が 0 なら 1 を返す", () => {
    expect(sharpnessScaleMax([])).toBe(1);
    expect(sharpnessScaleMax([{ sharpness: 0, ref: 0 }])).toBe(1);
  });
});

describe("diffScaleMax", () => {
  it("差分の 99% の位置の値としきい値の大きいほう × 1.1 を返す", () => {
    const frames = Array.from({ length: 200 }, (_, index) => ({ diff: index / 100 }));
    // 昇順の位置 floor(199 × 0.99) = 197 の値は 1.97
    expect(diffScaleMax(frames, 0.5)).toBeCloseTo(1.97 * 1.1);
  });

  it("差分がしきい値より小さければ、しきい値 × 1.1 を返す", () => {
    expect(diffScaleMax([{ diff: 0.1 }, { diff: null }], 0.5)).toBeCloseTo(0.55);
  });

  it("差分もしきい値も 0 なら 1 を返す", () => {
    expect(diffScaleMax([{ diff: null }], 0)).toBe(1);
  });
});

describe("chartX と chartY", () => {
  it("時刻を 0〜1000 の x にする", () => {
    expect(chartX(15, { from: 10, to: 20 })).toBe(500);
    expect(chartX(5, { from: 10, to: 20 })).toBe(-500);
  });

  it("値を下端 100・上端 0 の y にし、0〜上限に切り詰める", () => {
    expect(chartY(0, 10)).toBe(100);
    expect(chartY(10, 10)).toBe(0);
    expect(chartY(2.5, 10)).toBe(75);
    expect(chartY(20, 10)).toBe(0);
    expect(chartY(-1, 10)).toBe(100);
  });
});

describe("polylineSegments", () => {
  const range = { from: 0, to: 4 };
  const items = [
    { t: 0, value: 0 },
    { t: 1, value: 5 },
    { t: 2, value: null },
    { t: 3, value: 10 },
    { t: 10 / 3, value: 2 },
  ];

  it("値が null の点で線を切り、x・y を小数 1 桁に丸める", () => {
    expect(polylineSegments(items, (item) => item.value, range, 10)).toEqual([
      "0,100 250,50",
      "750,0 833.3,80",
    ]);
  });

  it("点が 1 つだけの区間は入れない", () => {
    expect(
      polylineSegments(
        [
          { t: 0, value: 1 },
          { t: 1, value: null },
          { t: 2, value: 1 },
        ],
        (item) => item.value,
        range,
        10,
      ),
    ).toEqual([]);
  });
});

describe("nearestIndexWithin", () => {
  const times = [0, 1, 2, 4, 8];

  it("t に最も近い位置を返す", () => {
    expect(nearestIndexWithin(times, 0, 5, 2.9)).toBe(2);
    expect(nearestIndexWithin(times, 0, 5, 3.1)).toBe(3);
  });

  it("同じ距離なら前の位置を返す", () => {
    expect(nearestIndexWithin(times, 0, 5, 3)).toBe(2);
  });

  it("start〜end - 1 の外の位置は返さない", () => {
    expect(nearestIndexWithin(times, 1, 3, 7)).toBe(2);
    expect(nearestIndexWithin(times, 1, 3, -5)).toBe(1);
  });

  it("start >= end なら -1 を返す", () => {
    expect(nearestIndexWithin(times, 2, 2, 1)).toBe(-1);
  });
});
