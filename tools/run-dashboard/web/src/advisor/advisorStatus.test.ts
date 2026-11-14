// advisorStatus のテスト。役割ごとの実行中・最後に終わった実行・実行なしの判定を確かめる
import { describe, expect, it } from "vitest";
import type { RoleName, SummaryAdvisorRun } from "../api/apiTypes.ts";
import { advisorStatusAt } from "./advisorStatus.ts";

// テスト用の advisor の実行。時刻のほかは固定の値にする
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
  returncode: tEnd === null ? null : 0,
  denials: 0,
  result: null,
});

describe("advisorStatusAt", () => {
  it("実行が無ければ、ROLE_ORDER の順に 4 件の none を返す", () => {
    expect(advisorStatusAt([], 10)).toEqual([
      { role: "controls", state: "none", run: null },
      { role: "strategy", state: "none", run: null },
      { role: "advice", state: "none", run: null },
      { role: "tips", state: "none", run: null },
    ]);
  });

  it("t_start <= t < t_end の実行は running (始まりを含む)", () => {
    const controls = run("advisor-controls-001", "controls", 10, 20);
    expect(advisorStatusAt([controls], 10)).toEqual([
      { role: "controls", state: "running", run: controls },
      { role: "strategy", state: "none", run: null },
      { role: "advice", state: "none", run: null },
      { role: "tips", state: "none", run: null },
    ]);
  });

  it("t_end と等しい時刻では running ではなく finished", () => {
    const controls = run("advisor-controls-001", "controls", 10, 20);
    expect(advisorStatusAt([controls], 20)[0]).toEqual({
      role: "controls",
      state: "finished",
      run: controls,
    });
  });

  it("t_end が null の実行は、t_start 以後ずっと running", () => {
    const tips = run("advisor-tips-001", "tips", 10, null);
    expect(advisorStatusAt([tips], 1000)[3]).toEqual({ role: "tips", state: "running", run: tips });
  });

  it("t_start が null の実行は running にしない", () => {
    const advice = run("advisor-advice-001", "advice", null, null);
    expect(advisorStatusAt([advice], 10)[2]).toEqual({ role: "advice", state: "none", run: null });
  });

  it("running の実行が複数あれば、t_start が最大の実行を返す", () => {
    const earlier = run("advisor-controls-001", "controls", 10, 50);
    const later = run("advisor-controls-002", "controls", 20, 40);
    expect(advisorStatusAt([earlier, later], 30)[0]).toEqual({
      role: "controls",
      state: "running",
      run: later,
    });
  });

  it("running の実行があれば、終わった実行があっても running を返す", () => {
    const finished = run("advisor-strategy-001", "strategy", 0, 5);
    const running = run("advisor-strategy-002", "strategy", 10, null);
    expect(advisorStatusAt([finished, running], 12)[1]).toEqual({
      role: "strategy",
      state: "running",
      run: running,
    });
  });

  it("running が無ければ、t_end <= t の実行のうち t_end が最大の実行を finished で返す", () => {
    const longer = run("advisor-advice-001", "advice", 0, 8);
    const shorter = run("advisor-advice-002", "advice", 2, 6);
    expect(advisorStatusAt([longer, shorter], 10)[2]).toEqual({
      role: "advice",
      state: "finished",
      run: longer,
    });
  });

  it("t_start が null でも t_end <= t なら finished", () => {
    const advice = run("advisor-advice-001", "advice", null, 5);
    expect(advisorStatusAt([advice], 10)[2]).toEqual({
      role: "advice",
      state: "finished",
      run: advice,
    });
  });

  it("始まる前の実行しか無ければ none", () => {
    const controls = run("advisor-controls-001", "controls", 10, 20);
    expect(advisorStatusAt([controls], 5)[0]).toEqual({
      role: "controls",
      state: "none",
      run: null,
    });
  });

  it("実行は自分の役割の行にだけ数える", () => {
    const tips = run("advisor-tips-001", "tips", 0, null);
    const strategy = run("advisor-strategy-001", "strategy", 0, 3);
    expect(advisorStatusAt([tips, strategy], 5)).toEqual([
      { role: "controls", state: "none", run: null },
      { role: "strategy", state: "finished", run: strategy },
      { role: "advice", state: "none", run: null },
      { role: "tips", state: "running", run: tips },
    ]);
  });
});
