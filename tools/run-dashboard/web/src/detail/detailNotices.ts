// 詳細ペインのタブに描くものが無いときに出す文言 (「記録なし」「形が古いため表示しない」など) を決める純粋な関数。
import type { RangeResponse } from "../api/apiTypes.ts";
import { NO_RECORD_NOTICE, OLD_FORMAT_NOTICE, rangeLaneNotice } from "../timeline/laneNotice.ts";

/** 要求の記録が無い実行の文言 */
export const NO_REQUEST_RECORD_NOTICE = "要求の記録なし";
/** 要求が 1 件も無い (選べる要求が無い) ときの文言 */
export const NO_REQUESTS_NOTICE = "要求なし";

/** 区間 API の応答のうち、文言を決めるのに使う部分 */
type RangeAvailability = Pick<RangeResponse<unknown>, "available" | "reason">;

/**
 * 要求のタブの文言を返す。古い形の実行 (oldFormat か、requests の reason が "old_format") は OLD_FORMAT_NOTICE、
 * 要求の記録が無ければ NO_REQUEST_RECORD_NOTICE、選んでいる要求が無ければ NO_REQUESTS_NOTICE、それ以外は null
 */
export const requestTabNotice = (
  oldFormat: boolean,
  requests: RangeAvailability,
  hasSelectedRequest: boolean,
): string | null => {
  if (oldFormat || requests.reason === "old_format") {
    return OLD_FORMAT_NOTICE;
  }
  if (!requests.available) {
    return NO_REQUEST_RECORD_NOTICE;
  }
  return hasSelectedRequest ? null : NO_REQUESTS_NOTICE;
};

/**
 * フレームのタブの文言を返す。rangeLaneNotice(frames) が null でなければそれ、時刻のあるフレームが無ければ
 * NO_RECORD_NOTICE、それ以外は null
 */
export const frameTabNotice = (frames: RangeAvailability, frameCount: number): string | null =>
  rangeLaneNotice(frames) ?? (frameCount === 0 ? NO_RECORD_NOTICE : null);

/**
 * advisor のタブの文言を返す。古い形の実行は OLD_FORMAT_NOTICE、advisor の実行も advisor 以外の書き換えも無ければ
 * NO_RECORD_NOTICE、それ以外は null
 */
export const advisorTabNotice = (
  oldFormat: boolean,
  runCount: number,
  externalRewriteCount: number,
): string | null => {
  if (oldFormat) {
    return OLD_FORMAT_NOTICE;
  }
  return runCount === 0 && externalRewriteCount === 0 ? NO_RECORD_NOTICE : null;
};

/** ファイルのタブの文言を返す。古い形の実行は OLD_FORMAT_NOTICE、それ以外は null */
export const fileTabNotice = (oldFormat: boolean): string | null =>
  oldFormat ? OLD_FORMAT_NOTICE : null;
