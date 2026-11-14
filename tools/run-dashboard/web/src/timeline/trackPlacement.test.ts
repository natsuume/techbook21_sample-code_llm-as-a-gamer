// trackPlacement のテスト。区間と点の位置の比率、表示の区間への切り詰め、再生位置のラベルの側を確かめる
import { describe, expect, it } from "vitest";
import {
  placePoint,
  placeSpan,
  playheadLabelSide,
  spansContaining,
  timeAtRatio,
} from "./trackPlacement.ts";

const range = { from: 10, to: 20 };

describe("placeSpan", () => {
  it("区間の中にある区間は、左端と幅を比率で返す", () => {
    expect(placeSpan(12, 15, range)).toEqual({ left: 0.2, width: 0.3 });
  });

  it("表示の区間の左にはみ出した区間は、左端を from に切り詰める", () => {
    expect(placeSpan(5, 12, range)).toEqual({ left: 0, width: 0.2 });
  });

  it("表示の区間の右にはみ出した区間は、右端を to に切り詰める", () => {
    expect(placeSpan(18, 30, range)).toEqual({ left: 0.8, width: 0.2 });
  });

  it("長さ 0 の区間は幅 0 で返す", () => {
    expect(placeSpan(15, 15, range)).toEqual({ left: 0.5, width: 0 });
  });

  it("端で接する区間は重なるものとして返す", () => {
    expect(placeSpan(5, 10, range)).toEqual({ left: 0, width: 0 });
    expect(placeSpan(20, 25, range)).toEqual({ left: 1, width: 0 });
  });

  it("重ならない区間は null を返す", () => {
    expect(placeSpan(1, 9.5, range)).toBeNull();
    expect(placeSpan(20.5, 30, range)).toBeNull();
  });

  it("表示の区間の幅が 0 なら null を返す", () => {
    expect(placeSpan(1, 2, { from: 1, to: 1 })).toBeNull();
  });
});

describe("placePoint", () => {
  it("区間の中の点は比率を返す (両端を含む)", () => {
    expect(placePoint(15, range)).toBe(0.5);
    expect(placePoint(10, range)).toBe(0);
    expect(placePoint(20, range)).toBe(1);
  });

  it("区間の外の点は null を返す", () => {
    expect(placePoint(9.99, range)).toBeNull();
    expect(placePoint(20.01, range)).toBeNull();
  });

  it("表示の区間の幅が 0 なら null を返す", () => {
    expect(placePoint(1, { from: 1, to: 1 })).toBeNull();
  });
});

describe("timeAtRatio", () => {
  it("比率の位置の時刻を返し、0〜1 の外の比率は切り詰める", () => {
    expect(timeAtRatio(0.25, range)).toBe(12.5);
    expect(timeAtRatio(-1, range)).toBe(10);
    expect(timeAtRatio(2, range)).toBe(20);
  });
});

describe("spansContaining", () => {
  const spans = [
    { name: "a", start: 1, end: 3 },
    { name: "b", start: 2, end: 2.5 },
    { name: "c", start: 3, end: 4 },
  ];

  it("時刻を含む区間を渡した順に返す (終わりの時刻は含まない)", () => {
    expect(spansContaining(spans, 2.2).map((span) => span.name)).toEqual(["a", "b"]);
    expect(spansContaining(spans, 3).map((span) => span.name)).toEqual(["c"]);
  });

  it("含む区間が無ければ空を返す", () => {
    expect(spansContaining(spans, 5)).toEqual([]);
  });
});

describe("playheadLabelSide", () => {
  it("比率が 0.85 以下なら線の右に置く", () => {
    expect(playheadLabelSide(0)).toBe("right");
    expect(playheadLabelSide(0.85)).toBe("right");
  });

  it("比率が 0.85 より大きければ線の左に置く", () => {
    expect(playheadLabelSide(0.86)).toBe("left");
    expect(playheadLabelSide(1)).toBe("left");
  });
});
