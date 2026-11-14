// 上部のバーの「ゲームの情報の最後の書き換え」の chip に出す文言と、押したときに開く版を決める純粋な関数。
import type { NoteKind } from "../api/apiTypes.ts";
import { formatSeconds } from "../format/formatValues.ts";
import type { NoteRewrite } from "./noteHistory.ts";

/** 最後の書き換え (lastRewriteAtOrBefore の結果) */
export type LastRewrite = { version: number; t: number; rewrites: NoteRewrite[] } | null;

/**
 * 書き換えの chip。external は受け持ちの advisor 以外による書き換えを含むか。open は押したときにファイルのタブで
 * 開く版 (null は押せない文言にする)
 */
export type RewriteChip = {
  text: string;
  external: boolean;
  open: { kind: NoteKind; version: number } | null;
};

/** 受け持ちの advisor 以外による書き換えを表す文言 */
const EXTERNAL_TEXT = "advisor 以外";

/** 値を、最初に現れた順に重複を除いて返す */
const unique = <T>(values: readonly T[]): T[] => [...new Set(values)];

/**
 * 最後の書き換えから chip を返す。
 * - null: "起動時のまま"、open は null
 * - それ以外: "<ファイル> 更新 t=<t> (<書き換えた者>)"。ファイルは書き換えた kind の "<kind>.md" を重複を除いて
 *   ", " でつなぐ。書き換えた者は、external を含めば "advisor 以外"、含まなければ by を重複を除いて ", " でつなぐ。
 *   open は、external を含めば最初の external の書き換えの kind、含まなければ最初の書き換えの kind と、その版
 */
export const rewriteChipOf = (last: LastRewrite): RewriteChip => {
  const first = last?.rewrites[0];
  if (last === null || first === undefined) {
    return { text: "起動時のまま", external: false, open: null };
  }
  const firstExternal = last.rewrites.find((rewrite) => rewrite.external);
  const files = unique(last.rewrites.map((rewrite) => `${rewrite.kind}.md`)).join(", ");
  const by =
    firstExternal === undefined
      ? unique(last.rewrites.map((rewrite) => rewrite.by)).join(", ")
      : EXTERNAL_TEXT;
  return {
    text: `${files} 更新 t=${formatSeconds(last.t)} (${by})`,
    external: firstExternal !== undefined,
    open: { kind: (firstExternal ?? first).kind, version: last.version },
  };
};
