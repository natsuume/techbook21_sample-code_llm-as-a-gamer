// laneNotice のテスト。記録の有無と古い形の実行で、レーンの文言が変わることを確かめる
import { describe, expect, it } from "vitest";
import type { SummaryAdvisorRun } from "../api/apiTypes.ts";
import type { NoteRewrite } from "../notes/noteHistory.ts";
import {
  advisorLaneNotice,
  controllerLaneNotice,
  NO_RECORD_NOTICE,
  OLD_FORMAT_NOTICE,
  rangeLaneNotice,
  requestValueLaneNotice,
} from "./laneNotice.ts";

const available = { available: true };
const missing = { available: false };
const oldFormat = { available: false, reason: "old_format" as const };

const advisorRun: SummaryAdvisorRun = {
  name: "advisor-tips-001",
  role: "tips",
  request: 1,
  t_start: 1,
  t_end: 2,
  returncode: 0,
  denials: 0,
  result: null,
};
const rewrite: NoteRewrite = {
  version: 1,
  t: 3,
  request: 2,
  kind: "tips",
  by: "external",
  external: true,
};

describe("rangeLaneNotice", () => {
  it("記録があれば null、無ければ記録なし、古い形なら形が古いため表示しない", () => {
    expect(rangeLaneNotice(available)).toBeNull();
    expect(rangeLaneNotice(missing)).toBe(NO_RECORD_NOTICE);
    expect(rangeLaneNotice(oldFormat)).toBe(OLD_FORMAT_NOTICE);
  });
});

describe("requestValueLaneNotice", () => {
  it("古い形の実行なら、要求の記録によらず形が古いため表示しない", () => {
    expect(requestValueLaneNotice(true, available)).toBe(OLD_FORMAT_NOTICE);
    expect(requestValueLaneNotice(true, missing)).toBe(OLD_FORMAT_NOTICE);
  });

  it("古い形でなければ、要求の記録の有無で決める", () => {
    expect(requestValueLaneNotice(false, available)).toBeNull();
    expect(requestValueLaneNotice(false, missing)).toBe(NO_RECORD_NOTICE);
  });
});

describe("controllerLaneNotice", () => {
  it("pad か要求のどちらかの記録があれば null", () => {
    expect(controllerLaneNotice(available, missing)).toBeNull();
    expect(controllerLaneNotice(missing, available)).toBeNull();
  });

  it("どちらの記録も無ければ記録なし", () => {
    expect(controllerLaneNotice(missing, oldFormat)).toBe(NO_RECORD_NOTICE);
  });
});

describe("advisorLaneNotice", () => {
  it("古い形の実行なら形が古いため表示しない", () => {
    expect(advisorLaneNotice(true, [advisorRun], [rewrite])).toBe(OLD_FORMAT_NOTICE);
  });

  it("advisor の実行も書き換えも無ければ記録なし", () => {
    expect(advisorLaneNotice(false, [], [])).toBe(NO_RECORD_NOTICE);
  });

  it("advisor の実行か書き換えのどちらかがあれば null", () => {
    expect(advisorLaneNotice(false, [advisorRun], [])).toBeNull();
    expect(advisorLaneNotice(false, [], [rewrite])).toBeNull();
  });
});
