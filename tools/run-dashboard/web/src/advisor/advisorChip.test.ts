// advisorChip のテスト。役割の状態ごとの chip の文言・見た目・開く実行を確かめる
import { describe, expect, it } from "vitest";
import type { SummaryAdvisorRun } from "../api/apiTypes.ts";
import { advisorChipOf } from "./advisorChip.ts";

const run = (fields: Partial<SummaryAdvisorRun>): SummaryAdvisorRun => ({
  name: "advisor-tips-001",
  role: "tips",
  request: 1,
  t_start: 2,
  t_end: null,
  returncode: null,
  denials: null,
  result: null,
  ...fields,
});

describe("advisorChipOf", () => {
  it("実行中は「実行中」の文言と running の見た目で、その実行を開く", () => {
    expect(advisorChipOf({ role: "tips", state: "running", run: run({}) })).toEqual({
      role: "tips",
      text: "tips 実行中",
      tone: "running",
      openName: "advisor-tips-001",
    });
  });

  it("終わった実行は終わった時刻の文言で、その実行を開く", () => {
    expect(
      advisorChipOf({
        role: "controls",
        state: "finished",
        run: run({ name: "advisor-controls-002", role: "controls", t_end: 12.5 }),
      }),
    ).toEqual({
      role: "controls",
      text: "controls t=12.50 に終了",
      tone: "default",
      openName: "advisor-controls-002",
    });
  });

  it("実行が無ければ「なし」の文言で、開く実行は null", () => {
    expect(advisorChipOf({ role: "advice", state: "none", run: null })).toEqual({
      role: "advice",
      text: "advice なし",
      tone: "default",
      openName: null,
    });
  });
});
