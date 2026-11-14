// タイムラインの要求の棒の種類 (色の区別) を、要求の結果・advisor・決めた操作から決める純粋な関数。
import type { RequestItem } from "../api/apiTypes.ts";

/** 何も操作しない決定の操作の文 */
export const NO_OPERATION_ACTION = "押下 none / 短押し none";
export type RequestCategory = "selected" | "failed" | "advisor" | "operation" | "other";

/**
 * 要求の棒の種類を、上から順に判定して返す。
 * n === selectedN → selected、result !== "ok" → failed、advisor_accepted.length > 0 → advisor、
 * action !== null && action !== NO_OPERATION_ACTION → operation、それ以外 → other
 */
export const categorizeRequest = (
  request: RequestItem,
  selectedN: number | null,
): RequestCategory => {
  if (request.n === selectedN) {
    return "selected";
  }
  if (request.result !== "ok") {
    return "failed";
  }
  if (request.advisor_accepted.length > 0) {
    return "advisor";
  }
  if (request.action !== null && request.action !== NO_OPERATION_ACTION) {
    return "operation";
  }
  return "other";
};
