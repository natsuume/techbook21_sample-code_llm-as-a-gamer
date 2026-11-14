// ゲームの情報 (controls・tips・strategy・advice の 4 ファイル) の版の一覧から、書き換えの一覧と
// ファイルごとのまとめを求める純粋な関数。書き換えの印は、kind と同じ名前の役割の行に置く。
import { NOTE_KIND_ORDER, type NoteKind, type SummaryNoteVersion } from "../api/apiTypes.ts";

/** 1 ファイルの書き換え。external は受け持ちの advisor 以外による書き換え (by が "external") */
export type NoteRewrite = {
  version: number;
  t: number;
  request: number;
  kind: NoteKind;
  by: string;
  external: boolean;
};

/** 受け持ちの advisor 以外による書き換えを表す by の値 */
const EXTERNAL_BY = "external";

/** 1 つの版の changed を、changed の順に書き換えの一覧にする */
const rewritesOfVersion = (version: SummaryNoteVersion): NoteRewrite[] =>
  version.changed.map((change) => ({
    version: version.version,
    t: version.t,
    request: version.request,
    kind: change.kind,
    by: change.by,
    external: change.by === EXTERNAL_BY,
  }));

/** version の昇順に、各版の changed を順に展開した書き換えの一覧を返す。external は by === "external" */
export const listRewrites = (versions: readonly SummaryNoteVersion[]): NoteRewrite[] =>
  [...versions].sort((left, right) => left.version - right.version).flatMap(rewritesOfVersion);

/**
 * 時刻 t 以前 (版の t <= t) で changed が空でない版のうち、version が最大の版と、その版の書き換えを返す。
 * 無ければ null (起動時のまま)
 */
export const lastRewriteAtOrBefore = (
  versions: readonly SummaryNoteVersion[],
  t: number,
): { version: number; t: number; rewrites: NoteRewrite[] } | null => {
  let latest: SummaryNoteVersion | null = null;
  for (const version of versions) {
    if (
      version.t <= t &&
      version.changed.length > 0 &&
      (latest === null || version.version > latest.version)
    ) {
      latest = version;
    }
  }
  return latest === null
    ? null
    : { version: latest.version, t: latest.t, rewrites: rewritesOfVersion(latest) };
};

/** 1 ファイルのまとめ。versions はそのファイルの版の version、last は最後の書き換え */
export type NoteFileStats = { kind: NoteKind; versions: number[]; last: NoteRewrite | null };

/**
 * NOTE_KIND_ORDER の順に、ファイルごとのまとめを返す。versions は最小の version (最初の版) と、その kind が
 * changed にある版の version の昇順。last はその kind の最後の書き換え (無ければ null)。
 * 版が 1 つも無ければ versions は []
 */
export const noteFileStats = (versions: readonly SummaryNoteVersion[]): NoteFileStats[] => {
  const rewrites = listRewrites(versions);
  const firstVersion = versions.reduce<number | null>(
    (smallest, version) =>
      smallest === null ? version.version : Math.min(smallest, version.version),
    null,
  );
  return NOTE_KIND_ORDER.map((kind) => {
    const rewritesOfKind = rewrites.filter((rewrite) => rewrite.kind === kind);
    const versionSet = new Set(rewritesOfKind.map((rewrite) => rewrite.version));
    if (firstVersion !== null) {
      versionSet.add(firstVersion);
    }
    return {
      kind,
      versions: [...versionSet].sort((left, right) => left - right),
      last: rewritesOfKind.at(-1) ?? null,
    };
  });
};

/** 書き換えのうち、by が runName のもの (その advisor の実行による書き換え) を返す */
export const rewritesByRun = (rewrites: readonly NoteRewrite[], runName: string): NoteRewrite[] =>
  rewrites.filter((rewrite) => rewrite.by === runName);

/** 書き換えのうち、external のもの (受け持ちの advisor 以外による書き換え) を返す */
export const externalRewrites = (rewrites: readonly NoteRewrite[]): NoteRewrite[] =>
  rewrites.filter((rewrite) => rewrite.external);
