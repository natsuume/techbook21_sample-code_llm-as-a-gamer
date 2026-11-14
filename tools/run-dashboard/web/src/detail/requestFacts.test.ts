// requestFacts のテスト。要求の時刻の文と、ファイルの名前と種類の判定を確かめる
import { describe, expect, it } from "vitest";
import { fileNameOf, isImagePath, requestTimeText } from "./requestFacts.ts";

describe("requestTimeText", () => {
  it("送信と受信の時刻と result を返す", () => {
    expect(requestTimeText({ t_sent: 1, t_received: 1.25, result: "ok" })).toBe(
      "送信 t=1.00 → 受信 t=1.25 · ok",
    );
  });

  it("受信が無ければ受信なし", () => {
    expect(requestTimeText({ t_sent: 2, t_received: null, result: "error" })).toBe(
      "送信 t=2.00 → 受信なし · error",
    );
  });
});

describe("isImagePath・fileNameOf", () => {
  it("画像の拡張子なら true", () => {
    expect(isImagePath("advisor-tips-001/frame-01.jpg")).toBe(true);
    expect(isImagePath("a/b.JPEG")).toBe(true);
    expect(isImagePath("advisor-tips-001/context.md")).toBe(false);
    expect(isImagePath("advisor-tips-001/stream.jsonl")).toBe(false);
  });

  it("パスの最後の部分を返す", () => {
    expect(fileNameOf("advisor-tips-001/result.json")).toBe("result.json");
    expect(fileNameOf("result.json")).toBe("result.json");
  });
});
