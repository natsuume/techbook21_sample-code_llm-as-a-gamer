// ライブで、最後の要求の後の応答待ちの区間 (経過秒) を求める純粋な関数。
// 送った時刻の記録は応答を受けた後にしか書かれないため、最後の要求の受信から今までを応答待ちとみなす近似。
import type { RangeResponse, RequestItem } from "../api/apiTypes.ts";

/** 応答待ちの区間 (経過秒)。start < end */
export type PendingSpan = { start: number; end: number };

/**
 * 応答待ちの区間を返す。tEnd は今 (summary.t_end)。表示がライブのときだけ呼ぶ。
 * - requests.available が false か、items が空なら null
 * - 最後の要求 (配列の最後) の t_received ?? t_sent を start、tEnd ?? start を end とする
 *   (失敗した要求は受信時刻が無いため、送信時刻から)
 * - end <= start なら null
 */
export const pendingRequestSpan = (
  _requests: RangeResponse<RequestItem>,
  _tEnd: number | null,
): PendingSpan | null => {
  throw new Error("not implemented");
};
