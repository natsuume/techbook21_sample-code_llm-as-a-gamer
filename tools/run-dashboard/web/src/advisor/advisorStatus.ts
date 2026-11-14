// advisor の 4 つの役割ごとに、ある時刻に実行中の advisor か、最後に終わった advisor を求める純粋な関数。
import type { RoleName, SummaryAdvisorRun } from "../api/apiTypes.ts";

/** 役割の状態。running は実行中、finished は最後に終わった実行、none は実行が無い */
export type AdvisorRoleStatus = {
  role: RoleName;
  state: "running" | "finished" | "none";
  run: SummaryAdvisorRun | null;
};

/**
 * ROLE_ORDER の順に、役割ごとの時刻 t の状態を 4 件返す。
 * - running: t_start !== null && t_start <= t && (t_end === null || t < t_end) の実行のうち t_start が最大のもの
 * - finished (running が無いとき): t_end !== null && t_end <= t の実行のうち t_end が最大のもの
 * - どちらも無ければ none (run は null)
 */
export const advisorStatusAt: (
  runs: readonly SummaryAdvisorRun[],
  t: number,
) => AdvisorRoleStatus[] = () => {
  throw new Error("not implemented");
};
