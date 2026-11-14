// formatValues のテスト。秒・バイト数の書式と、文字数の数え方を確かめる
import { describe, expect, it } from "vitest";
import { countCharacters, formatBytes, formatSeconds } from "./formatValues.ts";

describe("formatSeconds", () => {
  it("null は — を返す", () => {
    expect(formatSeconds(null)).toBe("—");
    expect(formatSeconds(null, 1)).toBe("—");
  });

  it("既定では小数 2 桁で返す", () => {
    expect(formatSeconds(1.5)).toBe("1.50");
    expect(formatSeconds(0)).toBe("0.00");
  });

  it("digits の桁数で返す", () => {
    expect(formatSeconds(12.34, 1)).toBe("12.3");
    expect(formatSeconds(12.6, 0)).toBe("13");
    expect(formatSeconds(3, 3)).toBe("3.000");
  });

  it("ja-JP の書式で 3 桁ごとに区切る", () => {
    expect(formatSeconds(1234.5)).toBe("1,234.50");
  });
});

describe("formatBytes", () => {
  it("null は — を返す", () => {
    expect(formatBytes(null)).toBe("—");
  });

  it("1024 未満は B で返す", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(999)).toBe("999 B");
  });

  it("1024 以上 1024² 未満は KB を小数 1 桁で返す", () => {
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(1536)).toBe("1.5 KB");
  });

  it("KB の値も ja-JP の書式で 3 桁ごとに区切る", () => {
    expect(formatBytes(1023 * 1024)).toBe("1,023.0 KB");
  });

  it("1024² 以上は MB を小数 1 桁で返す", () => {
    expect(formatBytes(1024 * 1024)).toBe("1.0 MB");
    expect(formatBytes(2.5 * 1024 * 1024)).toBe("2.5 MB");
  });

  it("MB の値も ja-JP の書式で 3 桁ごとに区切る", () => {
    expect(formatBytes(2048 * 1024 * 1024)).toBe("2,048.0 MB");
  });
});

describe("countCharacters", () => {
  it("ASCII と日本語の文字数を返す", () => {
    expect(countCharacters("abc")).toBe(3);
    expect(countCharacters("日本語")).toBe(3);
  });

  it("サロゲートペアの文字を 1 文字と数える", () => {
    expect(countCharacters("😀")).toBe(1);
    expect(countCharacters("𠮷野家")).toBe(3);
  });

  it("空の文字列は 0", () => {
    expect(countCharacters("")).toBe(0);
  });
});
