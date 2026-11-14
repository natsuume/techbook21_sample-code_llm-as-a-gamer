// advisorRuns のテスト。役割ごとのカードのまとまり、時刻の文、書き換えのボタンの文を確かめる
import { describe, expect, it } from "vitest";
import type { SummaryAdvisorRun } from "../api/apiTypes.ts";
import type { NoteRewrite } from "../notes/noteHistory.ts";
import {
  advisorRoleGroups,
  advisorTimeText,
  externalRewriteButtonText,
  rewriteButtonText,
} from "./advisorRuns.ts";

const advisorRun = (
  name: string,
  role: SummaryAdvisorRun["role"],
  tStart: number | null,
  tEnd: number | null,
): SummaryAdvisorRun => ({
  name,
  role,
  request: 1,
  t_start: tStart,
  t_end: tEnd,
  returncode: tEnd === null ? null : 0,
  denials: null,
  result: null,
});

const rewrite = (version: number, by: string): NoteRewrite => ({
  version,
  t: version * 2,
  request: version,
  kind: "strategy",
  by,
  external: by === "external",
});

describe("advisorRoleGroups", () => {
  const runs = [
    advisorRun("advisor-strategy-002", "strategy", 9, null),
    advisorRun("advisor-controls-001", "controls", 3.5, null),
    advisorRun("advisor-strategy-001", "strategy", 1.25, 5.25),
    advisorRun("advisor-strategy-003", "strategy", null, null),
  ];
  const rewrites = [rewrite(1, "advisor-strategy-001"), rewrite(2, "external")];

  it("ROLE_ORDER の順に、役割ごとのカードを t_start の昇順で返す (null は最後)", () => {
    const groups = advisorRoleGroups(runs, rewrites);
    expect(groups.map((group) => group.role)).toEqual(["controls", "strategy", "advice", "tips"]);
    expect(groups[1]?.cards.map((card) => card.run.name)).toEqual([
      "advisor-strategy-001",
      "advisor-strategy-002",
      "advisor-strategy-003",
    ]);
    expect(groups[2]?.cards).toEqual([]);
  });

  it("その実行による書き換えと、実行中かを付ける", () => {
    const strategy = advisorRoleGroups(runs, rewrites)[1];
    expect(strategy?.cards[0]?.rewrites.map((item) => item.version)).toEqual([1]);
    expect(strategy?.cards[0]?.running).toBe(false);
    expect(strategy?.cards[1]?.rewrites).toEqual([]);
    expect(strategy?.cards[1]?.running).toBe(true);
  });

  it("渡した配列は並べ替えない", () => {
    const copy = [...runs];
    advisorRoleGroups(runs, rewrites);
    expect(runs).toEqual(copy);
  });
});

describe("advisorTimeText", () => {
  it("終わった実行は開始・終了・長さ", () => {
    expect(advisorTimeText({ t_start: 1.25, t_end: 5.25 })).toBe("t=1.25–5.25 (4.00 秒)");
  });

  it("終わりの記録が無い実行は実行中", () => {
    expect(advisorTimeText({ t_start: 3.5, t_end: null })).toBe("t=3.50– (実行中)");
  });

  it("開始が無ければ長さを出さない", () => {
    expect(advisorTimeText({ t_start: null, t_end: 5 })).toBe("t=—–5.00");
  });
});

describe("rewriteButtonText・externalRewriteButtonText", () => {
  it("版・時刻・ファイルを返す", () => {
    expect(rewriteButtonText(rewrite(1, "advisor-strategy-001"))).toBe(
      "版 1 · t=2.00 · strategy.md",
    );
  });

  it("advisor 以外の書き換えは、書き換えた者を足す", () => {
    expect(externalRewriteButtonText(rewrite(2, "external"))).toBe(
      "版 2 · t=4.00 · strategy.md (advisor 以外)",
    );
  });
});
