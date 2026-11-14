import { describe, expect, it } from "vitest";
import { formatRunOption } from "./runOption.ts";

describe("formatRunOption", () => {
  it("実行の名前・ゲーム・モデルを「 · 」でつなぐ", () => {
    expect(formatRunOption({ run: "20261113-120000", game: "smb", model: "jev" })).toBe(
      "20261113-120000 · smb · jev",
    );
  });

  it("ゲームとモデルが null なら「—」にする", () => {
    expect(formatRunOption({ run: "r", game: null, model: null })).toBe("r · — · —");
  });
});
