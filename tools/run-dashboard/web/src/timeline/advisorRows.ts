// タイムラインの advisor とファイルのレーンの、役割ごとの行 (advisor の実行の区間と、受け持ちのファイルの書き換え) を
// 作る純粋な関数。書き換えは、kind と同じ名前の役割の行に置く。
import { ROLE_ORDER, type RoleName, type SummaryAdvisorRun } from "../api/apiTypes.ts";
import type { NoteRewrite } from "../notes/noteHistory.ts";

/** advisor の実行の区間 [start, end]。running は終わっていない実行 (t_end が null で、end は実行の終わり) */
export type AdvisorSpan = { name: string; start: number; end: number; running: boolean };
/** 役割 1 つの行 */
export type AdvisorRow = { role: RoleName; spans: AdvisorSpan[]; rewrites: NoteRewrite[] };

/**
 * ROLE_ORDER の順に、役割ごとの行を返す。
 * - spans は role がその役割で t_start が null でない実行。end は t_end ?? tEnd (start より前なら start)、
 *   running は t_end === null。start の昇順 (同じ start は配列の順)
 * - rewrites は kind がその役割と同じ名前の書き換えを、渡した順のまま
 */
export const advisorRowsOf = (
  runs: readonly SummaryAdvisorRun[],
  rewrites: readonly NoteRewrite[],
  tEnd: number,
): AdvisorRow[] =>
  ROLE_ORDER.map((role) => {
    const spans: AdvisorSpan[] = [];
    for (const run of runs) {
      if (run.role !== role || run.t_start === null) {
        continue;
      }
      spans.push({
        name: run.name,
        start: run.t_start,
        end: Math.max(run.t_start, run.t_end ?? tEnd),
        running: run.t_end === null,
      });
    }
    // Array.prototype.sort は安定なため、同じ start の実行は配列の順のまま残る
    spans.sort((left, right) => left.start - right.start);
    return { role, spans, rewrites: rewrites.filter((rewrite) => rewrite.kind === role) };
  });
