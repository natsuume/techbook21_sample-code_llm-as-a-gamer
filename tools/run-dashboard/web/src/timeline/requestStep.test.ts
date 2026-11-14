// requestStep のテスト。移る先の要求と、記録が無い・端にいるときの理由を確かめる
import { describe, expect, it } from "vitest";
import type { RequestItem } from "../api/apiTypes.ts";
import { requestStep, STEP_REASONS } from "./requestStep.ts";

const request = (n: number): RequestItem => ({
  n,
  t_sent: n,
  t_received: n + 0.5,
  result: "ok",
  action: null,
  advisor_requested: [],
  advisor_accepted: [],
  p: null,
  adopted_count: null,
});

const available = { available: true, items: [request(3), request(5), request(9)] };

describe("requestStep", () => {
  it("前と次の要求の番号を返す", () => {
    expect(requestStep(available, 5, -1)).toEqual({ n: 3, reason: null });
    expect(requestStep(available, 5, 1)).toEqual({ n: 9, reason: null });
  });

  it("最初の要求では前に、最後の要求では次に移れない", () => {
    expect(requestStep(available, 3, -1)).toEqual({ n: null, reason: STEP_REASONS.first });
    expect(requestStep(available, 9, 1)).toEqual({ n: null, reason: STEP_REASONS.last });
  });

  it("要求の記録が無い実行では、形が古いかどうかで理由を変える", () => {
    expect(requestStep({ available: false, items: [] }, null, 1)).toEqual({
      n: null,
      reason: STEP_REASONS.noRecord,
    });
    expect(requestStep({ available: false, reason: "old_format", items: [] }, null, -1)).toEqual({
      n: null,
      reason: STEP_REASONS.oldFormat,
    });
  });

  it("要求が無いか、選んでいる要求が無ければ noRequests", () => {
    expect(requestStep({ available: true, items: [] }, null, 1)).toEqual({
      n: null,
      reason: STEP_REASONS.noRequests,
    });
    expect(requestStep(available, 4, 1)).toEqual({ n: null, reason: STEP_REASONS.noRequests });
  });
});
