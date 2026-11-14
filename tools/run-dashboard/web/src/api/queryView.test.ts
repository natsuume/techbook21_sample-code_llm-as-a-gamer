import { describe, expect, it, vi } from "vitest";
import { type QueryResultLike, toQueryView } from "./queryView.ts";

const result = (overrides: Partial<QueryResultLike<number>>): QueryResultLike<number> => ({
  status: "pending",
  fetchStatus: "idle",
  data: undefined,
  error: null,
  refetch: () => undefined,
  ...overrides,
});

describe("toQueryView", () => {
  it("読み込みを止めた pending (fetchStatus が idle) は idle", () => {
    expect(toQueryView(result({ status: "pending", fetchStatus: "idle" }))).toEqual({
      state: "idle",
    });
  });

  it("読み込み中の pending は loading", () => {
    expect(toQueryView(result({ status: "pending", fetchStatus: "fetching" }))).toEqual({
      state: "loading",
    });
    expect(toQueryView(result({ status: "pending", fetchStatus: "paused" }))).toEqual({
      state: "loading",
    });
  });

  it("success は data を持つ ready", () => {
    expect(toQueryView(result({ status: "success", fetchStatus: "idle", data: 3 }))).toEqual({
      state: "ready",
      data: 3,
    });
  });

  it("error は error を持ち、retry で refetch を呼ぶ", () => {
    const refetch = vi.fn();
    const error = new Error("失敗");
    const view = toQueryView(result({ status: "error", error, refetch, data: 3 }));
    expect(view.state).toBe("error");
    if (view.state === "error") {
      expect(view.error).toBe(error);
      view.retry();
    }
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});
