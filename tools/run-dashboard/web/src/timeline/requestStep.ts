// タイムラインの前の要求・次の要求のボタンで移る先の要求と、移れないときの理由を決める純粋な関数。
import type { RangeResponse, RequestItem } from "../api/apiTypes.ts";
import { adjacentRequestN } from "../requests/requestNavigation.ts";

/** 移る方向。-1 は前、1 は次 */
export type StepDirection = -1 | 1;

/** ボタンの状態。移れるときは n が移る先の要求の番号で reason が null、移れないときは n が null で reason が理由 */
export type RequestStep = { n: number; reason: null } | { n: null; reason: string };

/** 押せない理由の文言 */
export const STEP_REASONS = {
  oldFormat: "形が古い実行のため、要求を選べない",
  noRecord: "要求の記録が無いため、要求を選べない",
  noRequests: "要求が無いため、要求を選べない",
  first: "最初の要求を選んでいる",
  last: "最後の要求を選んでいる",
} as const;

/**
 * 前 (direction が -1) か次 (1) の要求のボタンの状態を返す。
 * - 要求の記録が無い (available が false): reason が "old_format" なら oldFormat、それ以外は noRecord
 * - 要求が無いか、selectedN が null か、selectedN の要求が無い: noRequests
 * - adjacentRequestN(items, selectedN, direction) が null (端にいる): 前は first、次は last
 * - それ以外: その要求の番号
 */
export const requestStep = (
  requests: RangeResponse<RequestItem>,
  selectedN: number | null,
  direction: StepDirection,
): RequestStep => {
  if (!requests.available) {
    return {
      n: null,
      reason: requests.reason === "old_format" ? STEP_REASONS.oldFormat : STEP_REASONS.noRecord,
    };
  }
  const items = requests.items;
  if (selectedN === null || !items.some((request) => request.n === selectedN)) {
    return { n: null, reason: STEP_REASONS.noRequests };
  }
  const n = adjacentRequestN(items, selectedN, direction);
  if (n === null) {
    return { n: null, reason: direction === -1 ? STEP_REASONS.first : STEP_REASONS.last };
  }
  return { n, reason: null };
};
