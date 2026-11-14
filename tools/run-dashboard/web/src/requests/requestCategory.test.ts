// requestCategory のテスト。種類の判定の順と、各種類の条件を確かめる
import { describe, expect, it } from "vitest";
import type { RequestItem } from "../api/apiTypes.ts";
import { categorizeRequest, NO_OPERATION_ACTION } from "./requestCategory.ts";

// テスト用の要求。指定しないキーは、何も操作しなかった成功した要求の値にする
const request = (fields: Partial<RequestItem>): RequestItem => ({
  n: 1,
  t_sent: 1,
  t_received: 2,
  result: "ok",
  action: NO_OPERATION_ACTION,
  advisor_requested: [],
  advisor_accepted: [],
  p: null,
  adopted_count: null,
  ...fields,
});

describe("categorizeRequest", () => {
  it("選んでいる要求は、ほかの条件に関わらず selected", () => {
    const failedWithAdvisor = request({
      n: 4,
      result: "error",
      advisor_accepted: ["controls"],
      action: "押下 A / 短押し none",
    });
    expect(categorizeRequest(failedWithAdvisor, 4)).toBe("selected");
  });

  it("選んでいない要求で result が ok でなければ failed (advisor・操作より優先)", () => {
    expect(
      categorizeRequest(
        request({
          n: 4,
          result: "error",
          advisor_accepted: ["tips"],
          action: "押下 A / 短押し none",
        }),
        null,
      ),
    ).toBe("failed");
    expect(categorizeRequest(request({ n: 4, result: "discarded" }), 5)).toBe("failed");
  });

  it("advisor_accepted が空でなければ advisor (操作より優先)", () => {
    expect(
      categorizeRequest(
        request({ advisor_accepted: ["strategy"], action: "押下 A+UP / 短押し B" }),
        null,
      ),
    ).toBe("advisor");
  });

  it("advisor_requested だけがあっても advisor にしない", () => {
    expect(categorizeRequest(request({ advisor_requested: ["advice"] }), null)).toBe("other");
  });

  it("何もしない操作でない操作を決めた要求は operation", () => {
    expect(categorizeRequest(request({ action: "押下 A+UP / 短押し B" }), null)).toBe("operation");
    expect(
      categorizeRequest(request({ action: "押下 LS:-0.333333,0.275 / 短押し none" }), null),
    ).toBe("operation");
  });

  it("何もしない操作の要求は other", () => {
    expect(categorizeRequest(request({ action: NO_OPERATION_ACTION }), null)).toBe("other");
  });

  it("action が null の要求は other", () => {
    expect(categorizeRequest(request({ action: null }), null)).toBe("other");
  });

  it("selectedN が別の要求なら selected にしない", () => {
    expect(categorizeRequest(request({ n: 1, action: null }), 2)).toBe("other");
  });
});
