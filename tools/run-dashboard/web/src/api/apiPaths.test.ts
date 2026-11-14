// apiPaths のテスト。パスの形と、実行の名前・advisor の名前・ファイルのパスの符号化を確かめる
import { describe, expect, it } from "vitest";
import {
  advisorPath,
  filePath,
  notePath,
  rangePath,
  requestDetailPath,
  runsPath,
  summaryPath,
} from "./apiPaths.ts";

describe("runsPath", () => {
  it("実行の一覧のパスを返す", () => {
    expect(runsPath()).toBe("/api/runs");
  });
});

describe("summaryPath", () => {
  it("実行の名前をパスに入れる", () => {
    expect(summaryPath("20260101-120000")).toBe("/api/runs/20260101-120000");
  });

  it("実行の名前を encodeURIComponent で符号化する", () => {
    expect(summaryPath("a b/c?d")).toBe("/api/runs/a%20b%2Fc%3Fd");
  });
});

describe("rangePath", () => {
  it.each([
    ["requests", "/api/runs/20260101-120000/requests"],
    ["frames", "/api/runs/20260101-120000/frames"],
    ["pad", "/api/runs/20260101-120000/pad"],
    ["events", "/api/runs/20260101-120000/events"],
  ] as const)("%s の区間 API のパスを from・to なしで返す", (kind, expected) => {
    expect(rangePath("20260101-120000", kind)).toBe(expected);
  });

  it("実行の名前を encodeURIComponent で符号化する", () => {
    expect(rangePath("a b", "frames")).toBe("/api/runs/a%20b/frames");
  });
});

describe("requestDetailPath", () => {
  it("要求の番号をパスに入れる", () => {
    expect(requestDetailPath("20260101-120000", 12)).toBe("/api/runs/20260101-120000/requests/12");
  });

  it("実行の名前を encodeURIComponent で符号化する", () => {
    expect(requestDetailPath("a#b", 0)).toBe("/api/runs/a%23b/requests/0");
  });
});

describe("notePath", () => {
  it("版の番号をパスに入れる", () => {
    expect(notePath("20260101-120000", 3)).toBe("/api/runs/20260101-120000/notes/3");
  });

  it("実行の名前を encodeURIComponent で符号化する", () => {
    expect(notePath("a/b", 0)).toBe("/api/runs/a%2Fb/notes/0");
  });
});

describe("advisorPath", () => {
  it("advisor の実行の名前をパスに入れる", () => {
    expect(advisorPath("20260101-120000", "advisor-controls-001")).toBe(
      "/api/runs/20260101-120000/advisor/advisor-controls-001",
    );
  });

  it("実行の名前と advisor の実行の名前を encodeURIComponent で符号化する", () => {
    expect(advisorPath("a b", "x/y z")).toBe("/api/runs/a%20b/advisor/x%2Fy%20z");
  });
});

describe("filePath", () => {
  it("ファイルのパスの / を符号化せずにつなぐ", () => {
    expect(filePath("20260101-120000", "thumbs/000123.jpg")).toBe(
      "/api/runs/20260101-120000/files/thumbs/000123.jpg",
    );
  });

  it("ファイルのパスの部分ごとに encodeURIComponent で符号化する", () => {
    expect(filePath("20260101-120000", "advisor-tips-002/my notes#1.md")).toBe(
      "/api/runs/20260101-120000/files/advisor-tips-002/my%20notes%231.md",
    );
  });

  it("実行の名前を encodeURIComponent で符号化する", () => {
    expect(filePath("a b", "frames/1.png")).toBe("/api/runs/a%20b/files/frames/1.png");
  });
});
