// ゲームの情報 (controls・tips・strategy・advice の 4 ファイル) の版の一覧から、書き換えの一覧と
// ファイルごとのまとめを求める純粋な関数。書き換えの印は、kind と同じ名前の役割の行に置く。
import type { NoteKind, SummaryNoteVersion } from "../api/apiTypes.ts";

/** 1 ファイルの書き換え。external は受け持ちの advisor 以外による書き換え (by が "external") */
export type NoteRewrite = {
  version: number;
  t: number;
  request: number;
  kind: NoteKind;
  by: string;
  external: boolean;
};

/** version の昇順に、各版の changed を順に展開した書き換えの一覧を返す。external は by === "external" */
export const listRewrites: (versions: readonly SummaryNoteVersion[]) => NoteRewrite[] = () => {
  throw new Error("not implemented");
};

/**
 * 時刻 t 以前 (版の t <= t) で changed が空でない版のうち、version が最大の版と、その版の書き換えを返す。
 * 無ければ null (起動時のまま)
 */
export const lastRewriteAtOrBefore: (
  versions: readonly SummaryNoteVersion[],
  t: number,
) => { version: number; t: number; rewrites: NoteRewrite[] } | null = () => {
  throw new Error("not implemented");
};

/** 1 ファイルのまとめ。versions はそのファイルの版の version、last は最後の書き換え */
export type NoteFileStats = { kind: NoteKind; versions: number[]; last: NoteRewrite | null };

/**
 * NOTE_KIND_ORDER の順に、ファイルごとのまとめを返す。versions は最小の version (最初の版) と、その kind が
 * changed にある版の version の昇順。last はその kind の最後の書き換え (無ければ null)。
 * 版が 1 つも無ければ versions は []
 */
export const noteFileStats: (versions: readonly SummaryNoteVersion[]) => NoteFileStats[] = () => {
  throw new Error("not implemented");
};

/** 書き換えのうち、by が runName のもの (その advisor の実行による書き換え) を返す */
export const rewritesByRun: (rewrites: readonly NoteRewrite[], runName: string) => NoteRewrite[] =
  () => {
    throw new Error("not implemented");
  };

/** 書き換えのうち、external のもの (受け持ちの advisor 以外による書き換え) を返す */
export const externalRewrites: (rewrites: readonly NoteRewrite[]) => NoteRewrite[] = () => {
  throw new Error("not implemented");
};
