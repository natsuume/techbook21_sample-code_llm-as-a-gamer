// axisTicks のテスト。区間の幅に応じた刻みの選び方と、区間に入る目盛りの時刻を確かめる
import { describe, expect, it } from "vitest";
import { computeAxisTicks } from "./axisTicks.ts";

describe("computeAxisTicks", () => {
  it("目盛りが 10 区切り以下になる最小の刻みを選ぶ (幅 30 秒なら 5 秒)", () => {
    expect(computeAxisTicks({ from: 0, to: 30 })).toEqual([0, 5, 10, 15, 20, 25, 30]);
  });

  it("幅 ÷ 刻みがちょうど 10 の刻みを選ぶ (幅 5 秒なら 0.5 秒)", () => {
    expect(computeAxisTicks({ from: 0, to: 5 })).toEqual([
      0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5,
    ]);
  });

  it("from + 幅 の丸めで to - from が幅をわずかに超えても、同じ刻みを選ぶ (幅 10 秒なら 1 秒)", () => {
    const from = 7.176;
    const to = from + 10; // to - from は 10.000000000000002
    expect(computeAxisTicks({ from, to })).toEqual([8, 9, 10, 11, 12, 13, 14, 15, 16, 17]);
  });

  it("幅 ÷ 刻みが 10 を越える刻みは選ばない (幅 5.5 秒なら 1 秒)", () => {
    expect(computeAxisTicks({ from: 0, to: 5.5 })).toEqual([0, 1, 2, 3, 4, 5]);
  });

  it("区間の始まりが刻みの倍数でなければ、区間に入る最初の倍数から始める", () => {
    expect(computeAxisTicks({ from: 85, to: 115 })).toEqual([85, 90, 95, 100, 105, 110, 115]);
    expect(computeAxisTicks({ from: 0.3, to: 4.6 })).toEqual([0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5]);
  });

  it("区間の両端が刻みの倍数なら、両端を目盛りに含める", () => {
    expect(computeAxisTicks({ from: 60, to: 180 })).toEqual([
      60, 75, 90, 105, 120, 135, 150, 165, 180,
    ]);
  });

  it("TICK_STEPS の最大の刻みで足りるときは 3600 秒を使う", () => {
    expect(computeAxisTicks({ from: 0, to: 36000 })).toEqual([
      0, 3600, 7200, 10800, 14400, 18000, 21600, 25200, 28800, 32400, 36000,
    ]);
  });

  it("TICK_STEPS のどの刻みでも 10 区切りを越えるときは、条件を満たす最小の 3600 の倍数を刻みにする", () => {
    // 100000 / 7200 は 10 を越え、100000 / 10800 は 10 以下
    expect(computeAxisTicks({ from: 0, to: 100000 })).toEqual([
      0, 10800, 21600, 32400, 43200, 54000, 64800, 75600, 86400, 97200,
    ]);
  });

  it("区間の両端が小数でも、区間に入る刻みの倍数をすべて返す", () => {
    const ticks = computeAxisTicks({ from: 1000.1, to: 1004.6 });
    expect(ticks).toEqual([1000.5, 1001, 1001.5, 1002, 1002.5, 1003, 1003.5, 1004, 1004.5]);
  });

  it("区間の端が有限の数でなければ [] を返す", () => {
    expect(computeAxisTicks({ from: 0, to: Number.NaN })).toEqual([]);
    expect(computeAxisTicks({ from: 0, to: Number.POSITIVE_INFINITY })).toEqual([]);
  });
});
