// 詳細ペインの advisor のタブ (役割ごとの実行のカード・途中のツール呼び出し・書き換え) に出す値を求める純粋な関数。
import {
  type AdvisorStep,
  ROLE_ORDER,
  type RoleName,
  type SummaryAdvisorRun,
} from "../api/apiTypes.ts";
import { formatSeconds } from "../format/formatValues.ts";
import { type NoteRewrite, rewritesByRun } from "../notes/noteHistory.ts";
import { rewriterText } from "./noteFiles.ts";

/** advisor の実行の 1 枚のカード。rewrites はその実行による書き換え、running は終わりの記録が無い実行 */
export type AdvisorRunCard = { run: SummaryAdvisorRun; rewrites: NoteRewrite[]; running: boolean };

/** 役割ごとのカードのまとまり */
export type AdvisorRoleGroup = { role: RoleName; cards: AdvisorRunCard[] };

/** t_start の昇順 (null は最後)、同じなら名前の順に比べる */
const compareRuns = (left: SummaryAdvisorRun, right: SummaryAdvisorRun): number => {
  if (left.t_start !== right.t_start) {
    if (left.t_start === null) {
      return 1;
    }
    if (right.t_start === null) {
      return -1;
    }
    return left.t_start - right.t_start;
  }
  return left.name.localeCompare(right.name);
};

/**
 * ROLE_ORDER の順に、役割ごとの実行のカードを返す。カードは t_start の昇順 (null は最後、同じなら名前の順)。
 * rewrites は rewritesByRun(rewrites, 実行の名前)、running は t_end が null の実行。実行の無い役割も cards を [] で返す
 */
export const advisorRoleGroups = (
  runs: readonly SummaryAdvisorRun[],
  rewrites: readonly NoteRewrite[],
): AdvisorRoleGroup[] =>
  ROLE_ORDER.map((role) => ({
    role,
    cards: runs
      .filter((run) => run.role === role)
      .sort(compareRuns)
      .map((run) => ({
        run,
        rewrites: rewritesByRun(rewrites, run.name),
        running: run.t_end === null,
      })),
  }));

/**
 * 実行の時刻の文。終わった実行は "t=<開始>–<終了> (<長さ> 秒)"、終わりの記録が無い実行は "t=<開始>– (実行中)"。
 * 開始が null なら開始を "—" にし、長さを出さない
 */
export const advisorTimeText = (run: Pick<SummaryAdvisorRun, "t_start" | "t_end">): string => {
  const start = formatSeconds(run.t_start);
  if (run.t_end === null) {
    return `t=${start}– (実行中)`;
  }
  const end = formatSeconds(run.t_end);
  return run.t_start === null
    ? `t=${start}–${end}`
    : `t=${start}–${end} (${formatSeconds(run.t_end - run.t_start)} 秒)`;
};

/** 途中のツール呼び出しの種類の文言 */
export const ADVISOR_STEP_KIND_TEXT: Record<AdvisorStep["kind"], string> = {
  tool_use: "呼び出し",
  tool_result: "結果",
  permission_denied: "拒否",
  result: "答え",
};

/** 書き換えのボタンの文 "版 <v> · t=<t> · <kind>.md" */
export const rewriteButtonText = (rewrite: NoteRewrite): string =>
  `版 ${rewrite.version} · t=${formatSeconds(rewrite.t)} · ${rewrite.kind}.md`;

/** advisor 以外の書き換えのボタンの文 "版 <v> · t=<t> · <kind>.md (<書き換えた者>)" */
export const externalRewriteButtonText = (rewrite: NoteRewrite): string =>
  `${rewriteButtonText(rewrite)} (${rewriterText(rewrite.by)})`;
