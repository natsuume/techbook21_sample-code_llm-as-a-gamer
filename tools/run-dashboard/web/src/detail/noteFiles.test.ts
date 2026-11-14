// noteFiles のテスト。版のボタン、表示部に出すものの決め方、差分と全文、4 ファイルの一覧、版の文を確かめる
import { describe, expect, it } from "vitest";
import type { SummaryNoteVersion } from "../api/apiTypes.ts";
import { noteFileStats } from "../notes/noteHistory.ts";
import {
  effectiveNoteVersion,
  fileSelectionKey,
  lastRewriteText,
  noteContentView,
  noteDirectoryPath,
  noteFilePath,
  noteFileRows,
  noteSelectionForVersion,
  noteVersionLabel,
  noteVersionOptions,
  noteVersionText,
  previousNoteVersion,
  resolveFileSelection,
  rewriterText,
} from "./noteFiles.ts";

const versions: SummaryNoteVersion[] = [
  { version: 0, t: 1, request: 1, initial: true, changed: [] },
  {
    version: 1,
    t: 6.5,
    request: 4,
    initial: false,
    changed: [{ kind: "strategy", by: "advisor-strategy-001" }],
  },
  {
    version: 2,
    t: 12.375,
    request: 5,
    initial: false,
    changed: [
      { kind: "tips", by: "external" },
      { kind: "strategy", by: "external" },
    ],
  },
];
const stats = noteFileStats(versions);

describe("rewriterText・noteFilePath", () => {
  it("external は advisor 以外、それ以外は by のまま", () => {
    expect(rewriterText("external")).toBe("advisor 以外");
    expect(rewriterText("advisor-tips-001")).toBe("advisor-tips-001");
  });

  it("ゲームの情報のファイルとディレクトリーのパスを返す", () => {
    expect(noteFilePath("smb", "tips")).toBe("game_notes/smb/tips.md");
    expect(noteFilePath(null, "tips")).toBe("game_notes/tips.md");
    expect(noteDirectoryPath("smb")).toBe("game_notes/smb/");
    expect(noteDirectoryPath(null)).toBe("game_notes/");
  });
});

describe("fileSelectionKey", () => {
  it("選択の種類ごとに文字列にする", () => {
    expect(fileSelectionKey(null)).toBe("");
    expect(fileSelectionKey({ type: "note", kind: "tips", version: 2 })).toBe("note:tips:2");
    expect(fileSelectionKey({ type: "advisorFile", path: "advisor-tips-001/context.md" })).toBe(
      "advisorFile:advisor-tips-001/context.md",
    );
  });
});

describe("noteVersionOptions・noteVersionLabel", () => {
  it("ファイルの版ごとに、時刻と書き換えた者を返す", () => {
    expect(noteVersionOptions(versions, "strategy", [0, 1, 2])).toEqual([
      { version: 0, t: 1, changedBy: null },
      { version: 1, t: 6.5, changedBy: "advisor-strategy-001" },
      { version: 2, t: 12.375, changedBy: "external" },
    ]);
    expect(noteVersionOptions(versions, "strategy", [9])).toEqual([
      { version: 9, t: null, changedBy: null },
    ]);
  });

  it("書き換えた版は時刻と書き換えた者、それ以外は起動時", () => {
    expect(noteVersionLabel({ version: 0, t: 1, changedBy: null })).toBe("版 0 · 起動時");
    expect(noteVersionLabel({ version: 2, t: 12.375, changedBy: "external" })).toBe(
      "版 2 · t=12.38 · advisor 以外",
    );
  });
});

describe("effectiveNoteVersion", () => {
  it("version 以下で最大の、そのファイルの版を返す", () => {
    expect(effectiveNoteVersion([0, 2], 1)).toBe(0);
    expect(effectiveNoteVersion([0, 2], 2)).toBe(2);
    expect(effectiveNoteVersion([0, 2], 5)).toBe(2);
  });

  it("version 以下の版が無ければ version のまま", () => {
    expect(effectiveNoteVersion([3], 1)).toBe(1);
    expect(effectiveNoteVersion([], 4)).toBe(4);
  });
});

describe("noteSelectionForVersion", () => {
  it("その版で書き換えた最初のファイルを開く", () => {
    expect(noteSelectionForVersion(versions, 2)).toEqual({
      type: "note",
      kind: "tips",
      version: 2,
    });
  });

  it("書き換えの無い版と、無い版は controls を開く", () => {
    expect(noteSelectionForVersion(versions, 0)).toEqual({
      type: "note",
      kind: "controls",
      version: 0,
    });
    expect(noteSelectionForVersion(versions, 7).kind).toBe("controls");
  });
});

describe("resolveFileSelection", () => {
  it("選んでいなければ、版のある最初のファイルの最後の版", () => {
    expect(resolveFileSelection(null, stats)).toEqual({
      type: "note",
      kind: "controls",
      version: 0,
    });
  });

  it("版が 1 つも無ければ null", () => {
    expect(resolveFileSelection(null, noteFileStats([]))).toBeNull();
  });

  it("ゲームの情報の版は、そのファイルの有効な版にする", () => {
    expect(resolveFileSelection({ type: "note", kind: "controls", version: 2 }, stats)).toEqual({
      type: "note",
      kind: "controls",
      version: 0,
    });
    expect(resolveFileSelection({ type: "note", kind: "strategy", version: 2 }, stats)).toEqual({
      type: "note",
      kind: "strategy",
      version: 2,
    });
  });

  it("advisor の入出力のファイルはそのまま", () => {
    const file = { type: "advisorFile" as const, path: "advisor-tips-001/context.md" };
    expect(resolveFileSelection(file, stats)).toBe(file);
  });
});

describe("previousNoteVersion", () => {
  it("1 つ前の版があればその番号、無ければ null", () => {
    expect(previousNoteVersion(versions, 2)).toBe(1);
    expect(previousNoteVersion(versions, 0)).toBeNull();
  });
});

describe("noteContentView", () => {
  it("全文の表示は中身をそのまま返す", () => {
    expect(noteContentView("a\nb", { kind: "text", text: "a" }, "full")).toEqual({
      mode: "full",
      text: "a\nb",
      notice: null,
    });
  });

  it("差分は 1 つ前の版との行の差分と、足した行・消した行の数", () => {
    expect(noteContentView("a\nc", { kind: "text", text: "a\nb" }, "diff")).toEqual({
      mode: "diff",
      lines: [
        { kind: "same", text: "a" },
        { kind: "del", text: "b" },
        { kind: "add", text: "c" },
      ],
      added: 1,
      removed: 1,
    });
  });

  it("1 つ前の版が無いか、ファイルに分けられなければ、理由を付けて全文にする", () => {
    expect(noteContentView("a", { kind: "none" }, "diff")).toEqual({
      mode: "full",
      text: "a",
      notice: "1 つ前の版が無いため、全文を表示する",
    });
    expect(noteContentView("a", { kind: "unsplit" }, "diff")).toMatchObject({
      mode: "full",
      notice: "1 つ前の版はファイルに分けられないため、全文を表示する",
    });
  });
});

describe("noteFileRows・lastRewriteText", () => {
  it("4 ファイルの文字数・版の数・最後の書き換えを返す", () => {
    const rows = noteFileRows(stats, {
      controls: "ジャンプ",
      tips: "",
      strategy: "右へ",
      advice: "なし",
    });
    expect(rows.map((row) => [row.fileName, row.characters, row.versionCount])).toEqual([
      ["controls.md", 4, 1],
      ["tips.md", 0, 2],
      ["strategy.md", 2, 3],
      ["advice.md", 2, 1],
    ]);
    expect(rows[2]?.last?.version).toBe(2);
  });

  it("版の中身が無ければ文字数は null", () => {
    expect(noteFileRows(stats, null).every((row) => row.characters === null)).toBe(true);
  });

  it("最後の書き換えの文", () => {
    expect(lastRewriteText(null)).toBe("起動時のまま");
    expect(lastRewriteText(stats[2]?.last ?? null)).toBe("t=12.38 に更新 (advisor 以外)");
  });
});

describe("noteVersionText", () => {
  it("版の時刻と書き換えを返す", () => {
    expect(noteVersionText(versions, 1)).toBe("版 1 · t=6.50 · strategy.md (advisor-strategy-001)");
    expect(noteVersionText(versions, 2)).toBe(
      "版 2 · t=12.38 · tips.md (advisor 以外), strategy.md (advisor 以外)",
    );
  });

  it("書き換えの無い版は起動時、無い版は番号だけ、null は —", () => {
    expect(noteVersionText(versions, 0)).toBe("版 0 · 起動時");
    expect(noteVersionText(versions, 5)).toBe("版 5");
    expect(noteVersionText(versions, null)).toBe("—");
  });
});
