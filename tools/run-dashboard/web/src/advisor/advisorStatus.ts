// advisor の 4 つの役割ごとに、ある時刻に実行中の advisor か、最後に終わった advisor を求める純粋な関数。
import { ROLE_ORDER, type RoleName, type SummaryAdvisorRun } from "../api/apiTypes.ts";

/** 役割の状態。running は実行中、finished は最後に終わった実行、none は実行が無い */
export type AdvisorRoleStatus = {
  role: RoleName;
  state: "running" | "finished" | "none";
  run: SummaryAdvisorRun | null;
};

/** runs のうち key の値が最大の実行を返す (同じ値なら配列の先のもの)。key が null の実行は数えない */
const runWithLargest = (
  runs: readonly SummaryAdvisorRun[],
  key: (run: SummaryAdvisorRun) => number | null,
): SummaryAdvisorRun | null => {
  let largest: { run: SummaryAdvisorRun; value: number } | null = null;
  for (const run of runs) {
    const value = key(run);
    if (value !== null && (largest === null || value > largest.value)) {
      largest = { run, value };
    }
  }
  return largest?.run ?? null;
};

/** 時刻 t に実行中か (t_start <= t で、終わっていないか t < t_end) */
const isRunningAt = (run: SummaryAdvisorRun, t: number): boolean =>
  run.t_start !== null && run.t_start <= t && (run.t_end === null || t < run.t_end);

/** 時刻 t までに終わったか (t_end <= t) */
const isFinishedBy = (run: SummaryAdvisorRun, t: number): boolean =>
  run.t_end !== null && run.t_end <= t;

/**
 * ROLE_ORDER の順に、役割ごとの時刻 t の状態を 4 件返す。
 * - running: t_start !== null && t_start <= t && (t_end === null || t < t_end) の実行のうち t_start が最大のもの
 * - finished (running が無いとき): t_end !== null && t_end <= t の実行のうち t_end が最大のもの
 * - どちらも無ければ none (run は null)
 */
export const advisorStatusAt = (
  runs: readonly SummaryAdvisorRun[],
  t: number,
): AdvisorRoleStatus[] =>
  ROLE_ORDER.map((role): AdvisorRoleStatus => {
    const runsOfRole = runs.filter((run) => run.role === role);
    const running = runWithLargest(
      runsOfRole.filter((run) => isRunningAt(run, t)),
      (run) => run.t_start,
    );
    if (running !== null) {
      return { role, state: "running", run: running };
    }
    const finished = runWithLargest(
      runsOfRole.filter((run) => isFinishedBy(run, t)),
      (run) => run.t_end,
    );
    if (finished !== null) {
      return { role, state: "finished", run: finished };
    }
    return { role, state: "none", run: null };
  });
