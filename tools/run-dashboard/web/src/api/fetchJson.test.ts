import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, describeError, fetchJson } from "./fetchJson.ts";

/** fetch を、渡した応答を返す関数に置き換え、呼び出しを記録する mock を返す */
const stubFetch = (response: Response) => {
  const fetchMock = vi.fn(async (_path: string, _init?: RequestInit) => response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchJson", () => {
  it("成功の応答の JSON を返す", async () => {
    stubFetch(new Response(JSON.stringify({ run: "a" }), { status: 200 }));
    await expect(fetchJson<{ run: string }>("/api/runs/a")).resolves.toEqual({ run: "a" });
  });

  it("path と signal を fetch に渡す", async () => {
    const fetchMock = stubFetch(new Response("[]", { status: 200 }));
    const controller = new AbortController();
    await fetchJson("/api/runs", controller.signal);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/runs");
    expect(fetchMock.mock.calls[0]?.[1]?.signal).toBe(controller.signal);
  });

  it("成功でない応答は、状態のコードと本文の先頭を含む ApiError を投げる", async () => {
    stubFetch(new Response("not found", { status: 404 }));
    const error = await fetchJson("/api/runs/x").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toBeInstanceOf(Error);
    expect((error as ApiError).status).toBe(404);
    expect((error as ApiError).message).toBe("HTTP 404: /api/runs/x not found");
  });

  it("本文の先頭は 200 文字までにする", async () => {
    stubFetch(new Response("x".repeat(300), { status: 500 }));
    const error = await fetchJson("/api/runs").catch((caught: unknown) => caught);
    expect((error as ApiError).message).toBe(`HTTP 500: /api/runs ${"x".repeat(200)}`);
  });

  it("本文が空なら状態のコードとパスだけにする", async () => {
    stubFetch(new Response("", { status: 503 }));
    const error = await fetchJson("/api/runs").catch((caught: unknown) => caught);
    expect((error as ApiError).message).toBe("HTTP 503: /api/runs");
  });
});

describe("describeError", () => {
  it("Error はその message を返す", () => {
    expect(describeError(new ApiError(404, "HTTP 404: /api/runs/x"))).toBe("HTTP 404: /api/runs/x");
    expect(describeError(new Error("network"))).toBe("network");
  });

  it("Error でない値は文字列にする", () => {
    expect(describeError("失敗")).toBe("失敗");
    expect(describeError(42)).toBe("42");
  });
});
