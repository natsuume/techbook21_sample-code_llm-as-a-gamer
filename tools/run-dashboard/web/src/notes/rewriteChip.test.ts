// rewriteChip のテスト。最後の書き換えの chip の文言・external の区別・開く版を確かめる
import { describe, expect, it } from "vitest";
import type { NoteRewrite } from "./noteHistory.ts";
import { rewriteChipOf } from "./rewriteChip.ts";

const rewrite = (fields: Partial<NoteRewrite>): NoteRewrite => ({
  version: 2,
  t: 7.5,
  request: 4,
  kind: "controls",
  by: "advisor-controls-001",
  external: false,
  ...fields,
});

describe("rewriteChipOf", () => {
  it("書き換えが無ければ「起動時のまま」で、開く版は null", () => {
    expect(rewriteChipOf(null)).toEqual({ text: "起動時のまま", external: false, open: null });
  });

  it("advisor の書き換えは、ファイル・時刻・書き換えた実行の名前を出し、その版を開く", () => {
    expect(rewriteChipOf({ version: 2, t: 7.5, rewrites: [rewrite({})] })).toEqual({
      text: "controls.md 更新 t=7.50 (advisor-controls-001)",
      external: false,
      open: { kind: "controls", version: 2 },
    });
  });

  it("複数のファイルの書き換えは、ファイルと書き換えた者を重複を除いてつなぐ", () => {
    const chip = rewriteChipOf({
      version: 3,
      t: 9,
      rewrites: [
        rewrite({ kind: "strategy", by: "advisor-strategy-001" }),
        rewrite({ kind: "advice", by: "advisor-strategy-001" }),
      ],
    });
    expect(chip.text).toBe("strategy.md, advice.md 更新 t=9.00 (advisor-strategy-001)");
    expect(chip.open).toEqual({ kind: "strategy", version: 3 });
  });

  it("external を含めば「advisor 以外」の文言にし、最初の external のファイルを開く", () => {
    expect(
      rewriteChipOf({
        version: 4,
        t: 11.25,
        rewrites: [
          rewrite({ kind: "controls" }),
          rewrite({ kind: "tips", by: "external", external: true }),
        ],
      }),
    ).toEqual({
      text: "controls.md, tips.md 更新 t=11.25 (advisor 以外)",
      external: true,
      open: { kind: "tips", version: 4 },
    });
  });
});
