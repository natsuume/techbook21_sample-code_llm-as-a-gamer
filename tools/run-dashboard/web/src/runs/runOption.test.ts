import { describe, expect, it } from "vitest";
import { formatRunOption } from "./runOption.ts";

describe("formatRunOption", () => {
  it("実行の名前・ゲーム・モデルを「 · 」でつなぐ", () => {
    expect(
      formatRunOption({ run: "20261113-120000", game: "smb", model: "jev", in_progress: false }),
    ).toBe("20261113-120000 · smb · jev");
  });

  it("ゲームとモデルが null なら「—」にする", () => {
    expect(formatRunOption({ run: "r", game: null, model: null, in_progress: false })).toBe(
      "r · — · —",
    );
  });

  it("実行中なら末尾に「 · 実行中」を足し、実行中でなければ足さない", () => {
    expect(formatRunOption({ run: "r", game: "smb", model: "jev", in_progress: true })).toBe(
      "r · smb · jev · 実行中",
    );
    expect(formatRunOption({ run: "r", game: "smb", model: "jev", in_progress: false })).toBe(
      "r · smb · jev",
    );
  });
});
