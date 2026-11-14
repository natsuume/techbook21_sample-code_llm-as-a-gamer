// タイムラインのレーンに描くものが無いときに出す文言 (「記録なし」「形が古いため表示しない」) を決める純粋な関数。
import type { RangeResponse, SummaryAdvisorRun } from "../api/apiTypes.ts";
import type { NoteRewrite } from "../notes/noteHistory.ts";

/** 記録が無いレーンの文言 */
export const NO_RECORD_NOTICE = "記録なし";
/** 古い形の実行で、表示しないレーンの文言 */
export const OLD_FORMAT_NOTICE = "形が古いため表示しない";

/** レーンの文言。null はレーンを描く */
export type LaneNotice = typeof NO_RECORD_NOTICE | typeof OLD_FORMAT_NOTICE | null;

/**
 * 区間 API の応答だけで描くレーン (要求・意味のあるフレーム・イベント) の文言を返す。
 * available なら null、reason が "old_format" なら OLD_FORMAT_NOTICE、それ以外は NO_RECORD_NOTICE
 */
export const rangeLaneNotice = (
  response: Pick<RangeResponse<unknown>, "available" | "reason">,
): LaneNotice => {
  if (response.available) {
    return null;
  }
  return response.reason === "old_format" ? OLD_FORMAT_NOTICE : NO_RECORD_NOTICE;
};

/**
 * 要求の問いの値で描くレーン (操作に採った数・ヒートマップ) の文言を返す。
 * 古い形の実行 (oldFormat) なら OLD_FORMAT_NOTICE、そうでなければ rangeLaneNotice(requests)
 */
export const requestValueLaneNotice = (
  oldFormat: boolean,
  requests: Pick<RangeResponse<unknown>, "available" | "reason">,
): LaneNotice => (oldFormat ? OLD_FORMAT_NOTICE : rangeLaneNotice(requests));

/**
 * コントローラー入力のレーンの文言を返す。pad か要求のどちらかの記録があれば null (pad を優先して描く)、
 * どちらも無ければ NO_RECORD_NOTICE
 */
export const controllerLaneNotice = (
  pad: Pick<RangeResponse<unknown>, "available">,
  requests: Pick<RangeResponse<unknown>, "available">,
): LaneNotice => (pad.available || requests.available ? null : NO_RECORD_NOTICE);

/**
 * advisor とファイルのレーンの文言を返す。古い形の実行なら OLD_FORMAT_NOTICE、advisor の実行もゲームの情報の
 * 書き換えも 1 つも無い実行なら NO_RECORD_NOTICE、それ以外は null (表示の区間に何も無くても役割ごとの行を描く)
 */
export const advisorLaneNotice = (
  oldFormat: boolean,
  advisorRuns: readonly SummaryAdvisorRun[],
  rewrites: readonly NoteRewrite[],
): LaneNotice => {
  if (oldFormat) {
    return OLD_FORMAT_NOTICE;
  }
  return advisorRuns.length === 0 && rewrites.length === 0 ? NO_RECORD_NOTICE : null;
};
