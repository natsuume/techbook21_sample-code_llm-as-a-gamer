import { describe, expect, it } from "vitest";
import { joinClassNames } from "./joinClassNames.ts";

describe("joinClassNames", () => {
  it("文字列の class 名を渡した順に空白でつなぐ", () => {
    expect(joinClassNames("a", "b", "c")).toBe("a b c");
  });

  it("false・null・undefined・空の文字列を除く", () => {
    expect(joinClassNames("a", false, null, undefined, "", "b")).toBe("a b");
  });

  it("何も残らなければ空の文字列を返す", () => {
    expect(joinClassNames(undefined, false)).toBe("");
  });
});
