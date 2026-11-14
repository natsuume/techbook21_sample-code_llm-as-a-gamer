// 上部のバーの advisor の状態の chip に出す文言と、押したときに開く advisor の実行を決める純粋な関数。
import type { RoleName } from "../api/apiTypes.ts";
import { formatSeconds } from "../format/formatValues.ts";
import type { AdvisorRoleStatus } from "./advisorStatus.ts";

/**
 * 役割の chip。tone の running は実行中の見た目。openName は押したときに advisor のタブで開く実行の名前
 * (null は押せない chip にする)
 */
export type AdvisorChip = {
  role: RoleName;
  text: string;
  tone: "running" | "default";
  openName: string | null;
};

/**
 * 役割の状態から chip を返す。
 * - running: "<役割> 実行中"、tone は running、openName は実行の名前
 * - finished: "<役割> t=<t_end> に終了" (t_end は formatSeconds)、openName は実行の名前
 * - none: "<役割> なし"、openName は null
 */
export const advisorChipOf = (status: AdvisorRoleStatus): AdvisorChip => {
  if (status.state === "running" && status.run !== null) {
    return {
      role: status.role,
      text: `${status.role} 実行中`,
      tone: "running",
      openName: status.run.name,
    };
  }
  if (status.state === "finished" && status.run !== null) {
    return {
      role: status.role,
      text: `${status.role} t=${formatSeconds(status.run.t_end)} に終了`,
      tone: "default",
      openName: status.run.name,
    };
  }
  return { role: status.role, text: `${status.role} なし`, tone: "default", openName: null };
};
