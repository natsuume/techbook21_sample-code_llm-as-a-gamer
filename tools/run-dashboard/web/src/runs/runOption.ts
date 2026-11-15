// 実行の一覧の 1 件を、実行の select の文言にする純粋な関数。
import type { RunListItem } from "../api/apiTypes.ts";

/** 値が無いときに表示する文字 */
const MISSING_TEXT = "—";
/** 実行中の実行の印 */
const IN_PROGRESS_TEXT = "実行中";

/** "<run> · <game> · <model>" を返す。game・model が null なら "—"。in_progress なら末尾に " · 実行中" を足す */
export const formatRunOption = (
  run: Pick<RunListItem, "run" | "game" | "model" | "in_progress">,
): string =>
  [
    run.run,
    run.game ?? MISSING_TEXT,
    run.model ?? MISSING_TEXT,
    ...(run.in_progress ? [IN_PROGRESS_TEXT] : []),
  ].join(" · ");
