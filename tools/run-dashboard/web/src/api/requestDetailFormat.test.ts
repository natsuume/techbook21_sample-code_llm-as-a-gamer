import { describe, expect, it } from "vitest";
import type { RequestDetail } from "./apiTypes.ts";
import { isOldFormatRequestDetail } from "./requestDetailFormat.ts";

const detail: RequestDetail = {
  n: 1,
  t_sent: 1,
  t_received: 1.25,
  result: "ok",
  action: "押下 A / 短押し none",
  advisor_requested: [],
  advisor_accepted: [],
  p: null,
  adopted_count: 1,
  images: [],
  timings: { select_s: null, encode_s: null, decide_s: 0.125, apply_s: null },
  payload_bytes: null,
  history_text: "",
  previous_operation_text: null,
  note_version: null,
  probabilities: null,
  choices: null,
  advisor_probabilities: null,
  adopted: null,
};

describe("isOldFormatRequestDetail", () => {
  it("古い形の実行の応答なら true", () => {
    expect(isOldFormatRequestDetail({ available: false, reason: "old_format" })).toBe(true);
  });

  it("要求の詳細なら false", () => {
    expect(isOldFormatRequestDetail(detail)).toBe(false);
  });
});
