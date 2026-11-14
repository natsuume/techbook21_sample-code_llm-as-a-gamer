// 要求の詳細の API の応答が、古い形の実行の応答か (要求の詳細が無いか) を判定する純粋な関数。
import type { RequestDetail, RequestDetailOldFormat } from "./apiTypes.ts";

/** 応答が古い形の実行の応答 ({ available: false, reason: "old_format" }) なら true */
export const isOldFormatRequestDetail = (
  detail: RequestDetail | RequestDetailOldFormat,
): detail is RequestDetailOldFormat => "available" in detail && detail.available === false;
