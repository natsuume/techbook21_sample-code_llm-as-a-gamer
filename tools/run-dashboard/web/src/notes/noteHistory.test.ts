// noteHistory のテスト。書き換えの展開、時刻以前の最後の書き換え、ファイルごとのまとめ、書き換えの絞り込みを確かめる
import { describe, expect, it } from "vitest";
import type { SummaryNoteVersion } from "../api/apiTypes.ts";
import {
  externalRewrites,
  lastRewriteAtOrBefore,
  listRewrites,
  type NoteRewrite,
  noteFileStats,
  rewritesByRun,
} from "./noteHistory.ts";

// 版 0 は起動時の版 (initial が true で changed が空)。版 3 は changed が空の版
const versions: SummaryNoteVersion[] = [
  { version: 0, t: 0.5, request: 1, initial: true, changed: [] },
  {
    version: 1,
    t: 10,
    request: 5,
    initial: false,
    changed: [
      { kind: "controls", by: "advisor-controls-001" },
      { kind: "tips", by: "external" },
    ],
  },
  {
    version: 2,
    t: 20,
    request: 9,
    initial: false,
    changed: [{ kind: "strategy", by: "advisor-strategy-001" }],
  },
  { version: 3, t: 30, request: 14, initial: false, changed: [] },
  {
    version: 4,
    t: 40,
    request: 20,
    initial: false,
    changed: [{ kind: "controls", by: "external" }],
  },
];

const controlsByAdvisor: NoteRewrite = {
  version: 1,
  t: 10,
  request: 5,
  kind: "controls",
  by: "advisor-controls-001",
  external: false,
};
const tipsByExternal: NoteRewrite = {
  version: 1,
  t: 10,
  request: 5,
  kind: "tips",
  by: "external",
  external: true,
};
const strategyByAdvisor: NoteRewrite = {
  version: 2,
  t: 20,
  request: 9,
  kind: "strategy",
  by: "advisor-strategy-001",
  external: false,
};
const controlsByExternal: NoteRewrite = {
  version: 4,
  t: 40,
  request: 20,
  kind: "controls",
  by: "external",
  external: true,
};

describe("listRewrites", () => {
  it("version の昇順に、各版の changed を順に展開する", () => {
    expect(listRewrites(versions)).toEqual([
      controlsByAdvisor,
      tipsByExternal,
      strategyByAdvisor,
      controlsByExternal,
    ]);
  });

  it("版の並びが version の順でなくても、version の昇順に並べる", () => {
    expect(listRewrites([...versions].reverse())).toEqual([
      controlsByAdvisor,
      tipsByExternal,
      strategyByAdvisor,
      controlsByExternal,
    ]);
  });

  it('by が "external" の書き換えだけを external にする', () => {
    const external = listRewrites(versions).map((rewrite) => rewrite.external);
    expect(external).toEqual([false, true, false, true]);
  });

  it("版が無ければ空の配列を返す", () => {
    expect(listRewrites([])).toEqual([]);
  });
});

describe("lastRewriteAtOrBefore", () => {
  it("時刻以前で changed が空でない版のうち、version が最大の版とその書き換えを返す", () => {
    expect(lastRewriteAtOrBefore(versions, 25)).toEqual({
      version: 2,
      t: 20,
      rewrites: [strategyByAdvisor],
    });
  });

  it("版の時刻と等しい時刻では、その版を含める", () => {
    expect(lastRewriteAtOrBefore(versions, 10)).toEqual({
      version: 1,
      t: 10,
      rewrites: [controlsByAdvisor, tipsByExternal],
    });
  });

  it("changed が空の版は飛ばす", () => {
    expect(lastRewriteAtOrBefore(versions, 35)).toEqual({
      version: 2,
      t: 20,
      rewrites: [strategyByAdvisor],
    });
  });

  it("版の並びが version の順でなくても、version が最大の版を返す", () => {
    expect(lastRewriteAtOrBefore([...versions].reverse(), 100)).toEqual({
      version: 4,
      t: 40,
      rewrites: [controlsByExternal],
    });
  });

  it("時刻以前に書き換えが無ければ null (起動時のまま) を返す", () => {
    expect(lastRewriteAtOrBefore(versions, 5)).toBeNull();
    expect(lastRewriteAtOrBefore([], 100)).toBeNull();
  });
});

describe("noteFileStats", () => {
  it("NOTE_KIND_ORDER の順に、最初の版と書き換えた版の version と最後の書き換えを返す", () => {
    expect(noteFileStats(versions)).toEqual([
      { kind: "controls", versions: [0, 1, 4], last: controlsByExternal },
      { kind: "tips", versions: [0, 1], last: tipsByExternal },
      { kind: "strategy", versions: [0, 2], last: strategyByAdvisor },
      { kind: "advice", versions: [0], last: null },
    ]);
  });

  it("版の並びが version の順でなくても、version の昇順に並べる", () => {
    expect(noteFileStats([...versions].reverse())).toEqual([
      { kind: "controls", versions: [0, 1, 4], last: controlsByExternal },
      { kind: "tips", versions: [0, 1], last: tipsByExternal },
      { kind: "strategy", versions: [0, 2], last: strategyByAdvisor },
      { kind: "advice", versions: [0], last: null },
    ]);
  });

  it("版が 1 つも無ければ、versions は [] で last は null", () => {
    expect(noteFileStats([])).toEqual([
      { kind: "controls", versions: [], last: null },
      { kind: "tips", versions: [], last: null },
      { kind: "strategy", versions: [], last: null },
      { kind: "advice", versions: [], last: null },
    ]);
  });
});

describe("rewritesByRun", () => {
  const rewrites = [controlsByAdvisor, tipsByExternal, strategyByAdvisor, controlsByExternal];

  it("by が実行の名前の書き換えだけを返す", () => {
    expect(rewritesByRun(rewrites, "advisor-controls-001")).toEqual([controlsByAdvisor]);
  });

  it("当てはまる書き換えが無ければ空の配列を返す", () => {
    expect(rewritesByRun(rewrites, "advisor-advice-001")).toEqual([]);
  });
});

describe("externalRewrites", () => {
  it("external の書き換えだけを順のまま返す", () => {
    expect(
      externalRewrites([controlsByAdvisor, tipsByExternal, strategyByAdvisor, controlsByExternal]),
    ).toEqual([tipsByExternal, controlsByExternal]);
  });

  it("external の書き換えが無ければ空の配列を返す", () => {
    expect(externalRewrites([controlsByAdvisor, strategyByAdvisor])).toEqual([]);
  });
});
