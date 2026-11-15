// shownRequest のテスト。前の要求の詳細を出している間は、その要求を出す要求にすることを確かめる
import { describe, expect, it } from "vitest";
import type { RequestItem } from "../api/apiTypes.ts";
import { requestShownWithDetail } from "./shownRequest.ts";

const request = (n: number): RequestItem => ({
  n,
  t_sent: n,
  t_received: n + 0.5,
  result: "ok",
  action: `操作 ${n}`,
  advisor_requested: [],
  advisor_accepted: [],
  p: null,
  adopted_count: null,
});

describe("requestShownWithDetail", () => {
  it("詳細が無いか、同じ番号の要求の詳細なら、選んでいる要求を返す", () => {
    const selected = request(5);
    expect(requestShownWithDetail(selected, null)).toBe(selected);
    expect(requestShownWithDetail(selected, request(5))).toBe(selected);
  });

  it("違う番号の要求の詳細 (新しい要求の詳細を読む間に残した前の詳細) なら、その詳細の要求を返す", () => {
    const previous = request(4);
    expect(requestShownWithDetail(request(5), previous)).toBe(previous);
  });
});
