// viewWidthOptions のテスト。幅の選択肢の文言と、value から幅を戻すことを確かめる
import { describe, expect, it } from "vitest";
import { parseViewWidth, VIEW_WIDTH_OPTIONS, viewWidthLabel } from "./viewWidthOptions.ts";

describe("viewWidthLabel", () => {
  it("秒・分・全体の文言にする", () => {
    expect(viewWidthLabel(10)).toBe("10 秒");
    expect(viewWidthLabel(30)).toBe("30 秒");
    expect(viewWidthLabel(120)).toBe("2 分");
    expect(viewWidthLabel("all")).toBe("全体");
  });
});

describe("VIEW_WIDTH_OPTIONS", () => {
  it("10 秒・30 秒・2 分・全体の順に並べる", () => {
    expect(VIEW_WIDTH_OPTIONS).toEqual([
      { width: 10, value: "10", label: "10 秒" },
      { width: 30, value: "30", label: "30 秒" },
      { width: 120, value: "120", label: "2 分" },
      { width: "all", value: "all", label: "全体" },
    ]);
  });
});

describe("parseViewWidth", () => {
  it("選択肢の value から幅を返す", () => {
    expect(parseViewWidth("120")).toBe(120);
    expect(parseViewWidth("all")).toBe("all");
  });

  it("選択肢に無い value は null", () => {
    expect(parseViewWidth("60")).toBeNull();
    expect(parseViewWidth("")).toBeNull();
  });
});
