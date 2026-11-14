// advisorRows のテスト。役割ごとの行の順、実行中の区間の終わり、書き換えの振り分けを確かめる
import { describe, expect, it } from "vitest";
import type { RoleName, SummaryAdvisorRun } from "../api/apiTypes.ts";
import type { NoteRewrite } from "../notes/noteHistory.ts";
import { advisorRowsOf } from "./advisorRows.ts";

const run = (
  name: string,
  role: RoleName,
  tStart: number | null,
  tEnd: number | null,
): SummaryAdvisorRun => ({
  name,
  role,
  request: 1,
  t_start: tStart,
  t_end: tEnd,
  returncode: null,
  denials: null,
  result: null,
});

const rewrite = (version: number, kind: NoteRewrite["kind"], by: string): NoteRewrite => ({
  version,
  t: version,
  request: version,
  kind,
  by,
  external: by === "external",
});

describe("advisorRowsOf", () => {
  it("controls・strategy・advice・tips の順に 4 行を返す", () => {
    expect(advisorRowsOf([], [], 10).map((row) => row.role)).toEqual([
      "controls",
      "strategy",
      "advice",
      "tips",
    ]);
  });

  it("実行を役割の行に start の順で入れ、t_start が null の実行は入れない", () => {
    const rows = advisorRowsOf(
      [
        run("advisor-strategy-002", "strategy", 8, 9),
        run("advisor-strategy-001", "strategy", 2, 4),
        run("advisor-tips-001", "tips", null, null),
      ],
      [],
      10,
    );
    expect(rows[1]?.spans).toEqual([
      { name: "advisor-strategy-001", start: 2, end: 4, running: false },
      { name: "advisor-strategy-002", start: 8, end: 9, running: false },
    ]);
    expect(rows[3]?.spans).toEqual([]);
  });

  it("終わっていない実行は、実行の終わりまでの区間にする", () => {
    const rows = advisorRowsOf([run("advisor-controls-001", "controls", 3, null)], [], 15);
    expect(rows[0]?.spans).toEqual([
      { name: "advisor-controls-001", start: 3, end: 15, running: true },
    ]);
  });

  it("実行の終わりが開始より前なら、区間の終わりを開始にそろえる", () => {
    const rows = advisorRowsOf([run("advisor-controls-001", "controls", 3, null)], [], 1);
    expect(rows[0]?.spans[0]?.end).toBe(3);
  });

  it("書き換えを、kind と同じ名前の役割の行に入れる", () => {
    const strategy = rewrite(1, "strategy", "advisor-strategy-001");
    const tips = rewrite(2, "tips", "external");
    const rows = advisorRowsOf([], [strategy, tips], 10);
    expect(rows[1]?.rewrites).toEqual([strategy]);
    expect(rows[3]?.rewrites).toEqual([tips]);
    expect(rows[0]?.rewrites).toEqual([]);
  });
});
