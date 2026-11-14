// 実行の一覧の 1 件を、実行の select の文言にする純粋な関数。
import type { RunListItem } from "../api/apiTypes.ts";

/** 値が無いときに表示する文字 */
const MISSING_TEXT = "—";

/** "<run> · <game> · <model>" を返す。game・model が null なら "—" */
export const formatRunOption = (run: Pick<RunListItem, "run" | "game" | "model">): string =>
  [run.run, run.game ?? MISSING_TEXT, run.model ?? MISSING_TEXT].join(" · ");
