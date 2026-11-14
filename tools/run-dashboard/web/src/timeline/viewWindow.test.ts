// viewWindow のテスト。表示する幅ごとの区間と、区間の中の位置の比を確かめる
import { describe, expect, it } from "vitest";
import { computeViewWindow, ratioInRange } from "./viewWindow.ts";

describe("computeViewWindow", () => {
  describe('幅が "all"', () => {
    it("0 から実行の終わりまでを返す", () => {
      expect(computeViewWindow(12, "all", 300)).toEqual({ from: 0, to: 300 });
    });

    it("tEnd が null なら end を 0 とみなし、{ from: 0, to: 1 } を返す", () => {
      expect(computeViewWindow(0, "all", null)).toEqual({ from: 0, to: 1 });
    });

    it("tEnd が 0 なら { from: 0, to: 1 } を返す", () => {
      expect(computeViewWindow(0, "all", 0)).toEqual({ from: 0, to: 1 });
    });

    it("tEnd が負なら end を 0 とみなし、{ from: 0, to: 1 } を返す", () => {
      expect(computeViewWindow(0, "all", -5)).toEqual({ from: 0, to: 1 });
    });
  });

  describe("数の幅", () => {
    it("再生位置を中心にした区間を返す", () => {
      expect(computeViewWindow(100, 30, 300)).toEqual({ from: 85, to: 115 });
    });

    it("区間の始まりが 0 より前になるときは 0 から始める", () => {
      expect(computeViewWindow(3, 30, 300)).toEqual({ from: 0, to: 30 });
    });

    it("区間の終わりが実行の終わりを越えるときは、終わりを実行の終わりにそろえる", () => {
      expect(computeViewWindow(295, 30, 300)).toEqual({ from: 270, to: 300 });
    });

    it("実行の長さが幅と等しいときは { from: 0, to: w } を返す", () => {
      expect(computeViewWindow(5, 10, 10)).toEqual({ from: 0, to: 10 });
    });

    it("実行の長さが幅より短いときは { from: 0, to: w } を返す", () => {
      expect(computeViewWindow(50, 120, 60)).toEqual({ from: 0, to: 120 });
    });

    it("tEnd が null なら end を 0 とみなし、{ from: 0, to: w } を返す", () => {
      expect(computeViewWindow(0, 10, null)).toEqual({ from: 0, to: 10 });
    });

    it("再生位置が実行の終わりより後でも、区間は実行の終わりまでに収める", () => {
      expect(computeViewWindow(500, 10, 100)).toEqual({ from: 90, to: 100 });
    });
  });
});

describe("ratioInRange", () => {
  it("区間の中の位置の比を返す", () => {
    expect(ratioInRange(25, { from: 10, to: 40 })).toBeCloseTo(0.5);
  });

  it("区間の始まりは 0、終わりは 1 を返す", () => {
    expect(ratioInRange(10, { from: 10, to: 40 })).toBeCloseTo(0);
    expect(ratioInRange(40, { from: 10, to: 40 })).toBeCloseTo(1);
  });

  it("区間より前は 0 に切り詰める", () => {
    expect(ratioInRange(5, { from: 10, to: 40 })).toBe(0);
  });

  it("区間より後は 1 に切り詰める", () => {
    expect(ratioInRange(100, { from: 10, to: 40 })).toBe(1);
  });
});
