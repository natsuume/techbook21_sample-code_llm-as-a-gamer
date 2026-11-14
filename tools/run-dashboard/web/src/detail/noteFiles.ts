// 詳細ペインのファイルのタブ (ゲームの情報の 4 ファイルの一覧・版の選択・差分と全文) と、要求のタブの「ゲームの情報の版」に
// 出す値を求める純粋な関数。
import {
  NOTE_KIND_ORDER,
  type NoteChange,
  type NoteKind,
  type SummaryNoteVersion,
} from "../api/apiTypes.ts";
import { countCharacters, formatSeconds } from "../format/formatValues.ts";
import { type DiffLine, diffLines } from "../notes/lineDiff.ts";
import type { NoteFileStats, NoteRewrite } from "../notes/noteHistory.ts";
import type { FileSelection } from "../state/viewState.ts";

/** 受け持ちの advisor 以外による書き換えを表す by の値と、その文言 */
const EXTERNAL_BY = "external";
const EXTERNAL_TEXT = "advisor 以外";

/** 書き換えた者の文言。by が "external" なら "advisor 以外"、それ以外は by */
export const rewriterText = (by: string): string => (by === EXTERNAL_BY ? EXTERNAL_TEXT : by);

/** ゲームの情報のディレクトリーのパス "game_notes/<game>/" (game が null なら "game_notes/") */
export const noteDirectoryPath = (game: string | null): string =>
  game === null ? "game_notes/" : `game_notes/${game}/`;

/** ゲームの情報のファイルのパス "game_notes/<game>/<kind>.md" (game が null なら "game_notes/<kind>.md") */
export const noteFilePath = (game: string | null, kind: NoteKind): string =>
  `${noteDirectoryPath(game)}${kind}.md`;

/**
 * ファイルの選択を、比べるための文字列にする。null は ""、ゲームの情報の版は "note:<kind>:<version>"、
 * advisor の入出力のファイルは "advisorFile:<path>"
 */
export const fileSelectionKey = (file: FileSelection | null): string => {
  if (file === null) {
    return "";
  }
  return file.type === "note" ? `note:${file.kind}:${file.version}` : `advisorFile:${file.path}`;
};

/** 1 ファイルの版のボタン。changedBy はその版でそのファイルを書き換えた者 (書き換えていない版は null) */
export type NoteVersionOption = { version: number; t: number | null; changedBy: string | null };

/**
 * kindVersions (noteFileStats の versions) の順に、版のボタンを返す。t は版の時刻 (noteVersions に無ければ null)、
 * changedBy はその版の changed のうち kind が一致する最初のものの by
 */
export const noteVersionOptions = (
  noteVersions: readonly SummaryNoteVersion[],
  kind: NoteKind,
  kindVersions: readonly number[],
): NoteVersionOption[] =>
  kindVersions.map((version) => {
    const found = noteVersions.find((item) => item.version === version);
    return {
      version,
      t: found?.t ?? null,
      changedBy: found?.changed.find((change) => change.kind === kind)?.by ?? null,
    };
  });

/**
 * 版のボタンの文言。書き換えた版は "版 <v> · t=<t> · <書き換えた者>"、書き換えていない版 (最初の版) は
 * "版 <v> · 起動時"
 */
export const noteVersionLabel = (option: NoteVersionOption): string =>
  option.changedBy === null
    ? `版 ${option.version} · 起動時`
    : `版 ${option.version} · t=${formatSeconds(option.t)} · ${rewriterText(option.changedBy)}`;

/**
 * version の時点で有効な、そのファイルの版を返す。kindVersions (昇順) のうち version 以下で最大の版。
 * 無ければ version をそのまま返す
 */
export const effectiveNoteVersion = (kindVersions: readonly number[], version: number): number => {
  let effective: number | null = null;
  for (const candidate of kindVersions) {
    if (candidate <= version && (effective === null || candidate > effective)) {
      effective = candidate;
    }
  }
  return effective ?? version;
};

/**
 * 版 version をファイルのタブで開くときの選択を返す。ファイルは、その版で書き換えたファイルのうち changed の最初のもの。
 * 書き換えの無い版 (と noteVersions に無い版) は NOTE_KIND_ORDER の最初のファイル
 */
export const noteSelectionForVersion = (
  noteVersions: readonly SummaryNoteVersion[],
  version: number,
): Extract<FileSelection, { type: "note" }> => {
  const changed = noteVersions.find((item) => item.version === version)?.changed[0];
  return { type: "note", kind: changed?.kind ?? NOTE_KIND_ORDER[0] ?? "controls", version };
};

/**
 * ファイルのタブの表示部に出すものを決める。
 * - file が null: stats のうち版のある最初のファイルの、最後の版。版のあるファイルが無ければ null
 * - ゲームの情報の版: そのファイルの effectiveNoteVersion (stats に無いファイルは版をそのまま使う)
 * - advisor の入出力のファイル: そのまま
 */
export const resolveFileSelection = (
  file: FileSelection | null,
  stats: readonly NoteFileStats[],
): FileSelection | null => {
  if (file === null) {
    const first = stats.find((stat) => stat.versions.length > 0);
    const last = first?.versions.at(-1);
    return first === undefined || last === undefined
      ? null
      : { type: "note", kind: first.kind, version: last };
  }
  if (file.type === "advisorFile") {
    return file;
  }
  const kindVersions = stats.find((stat) => stat.kind === file.kind)?.versions ?? [];
  return { ...file, version: effectiveNoteVersion(kindVersions, file.version) };
};

/** 差分の比べる先の版 (version - 1)。noteVersions にその版が無ければ null */
export const previousNoteVersion = (
  noteVersions: readonly SummaryNoteVersion[],
  version: number,
): number | null =>
  noteVersions.some((item) => item.version === version - 1) ? version - 1 : null;

/** 1 つ前の版の同じファイル。text は中身、none は 1 つ前の版が無い、unsplit はファイルに分けられない版 */
export type PreviousNoteText =
  | { kind: "text"; text: string }
  | { kind: "none" }
  | { kind: "unsplit" };

/** 表示部の中身。full は全文 (notice は全文にした理由)、diff は行の差分と足した行・消した行の数 */
export type NoteContentView =
  | { mode: "full"; text: string; notice: string | null }
  | { mode: "diff"; lines: DiffLine[]; added: number; removed: number };

/**
 * 表示部の中身を返す。view が "full" なら全文。"diff" なら previous との diffLines。previous が無い版と、
 * ファイルに分けられない版のときは、理由を notice に入れて全文にする
 */
export const noteContentView = (
  current: string,
  previous: PreviousNoteText,
  view: "diff" | "full",
): NoteContentView => {
  if (view === "full") {
    return { mode: "full", text: current, notice: null };
  }
  if (previous.kind === "none") {
    return { mode: "full", text: current, notice: "1 つ前の版が無いため、全文を表示する" };
  }
  if (previous.kind === "unsplit") {
    return {
      mode: "full",
      text: current,
      notice: "1 つ前の版はファイルに分けられないため、全文を表示する",
    };
  }
  const lines = diffLines(previous.text, current);
  return {
    mode: "diff",
    lines,
    added: lines.filter((line) => line.kind === "add").length,
    removed: lines.filter((line) => line.kind === "del").length,
  };
};

/** 4 ファイルの一覧の 1 行。characters は選んだ版の文字数 (版の中身が無ければ null) */
export type NoteFileRow = {
  kind: NoteKind;
  fileName: string;
  characters: number | null;
  versionCount: number;
  last: NoteRewrite | null;
};

/**
 * stats の順に、4 ファイルの一覧の行を返す。characters は files[kind] の countCharacters (files が null・undefined
 * なら null)、versionCount は版の数
 */
export const noteFileRows = (
  stats: readonly NoteFileStats[],
  files: Readonly<Record<NoteKind, string>> | null | undefined,
): NoteFileRow[] =>
  stats.map((stat) => {
    const text = files?.[stat.kind];
    return {
      kind: stat.kind,
      fileName: `${stat.kind}.md`,
      characters: text === undefined ? null : countCharacters(text),
      versionCount: stat.versions.length,
      last: stat.last,
    };
  });

/** 最後の書き換えの文。null は "起動時のまま"、それ以外は "t=<t> に更新 (<書き換えた者>)" */
export const lastRewriteText = (last: NoteRewrite | null): string =>
  last === null ? "起動時のまま" : `t=${formatSeconds(last.t)} に更新 (${rewriterText(last.by)})`;

/** 書き換えの文 "<kind>.md (<書き換えた者>)" */
const changeText = (change: NoteChange): string => `${change.kind}.md (${rewriterText(change.by)})`;

/**
 * 要求のタブの「ゲームの情報の版」の文。null は "—"。noteVersions に無い版は "版 <v>"。
 * 書き換えた版は "版 <v> · t=<t> · <書き換え>, …"、書き換えの無い版は "版 <v> · 起動時"
 */
export const noteVersionText = (
  noteVersions: readonly SummaryNoteVersion[],
  version: number | null,
): string => {
  if (version === null) {
    return "—";
  }
  const found = noteVersions.find((item) => item.version === version);
  if (found === undefined) {
    return `版 ${version}`;
  }
  if (found.changed.length === 0) {
    return `版 ${version} · 起動時`;
  }
  return `版 ${version} · t=${formatSeconds(found.t)} · ${found.changed.map(changeText).join(", ")}`;
};
