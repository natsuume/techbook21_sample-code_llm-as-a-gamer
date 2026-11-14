// 上の段の「選んでいる要求」に出す値 (advisor の問いの確率・問いの値の上位・画像の大きさの合計) を求める純粋な関数。
import {
  type QuestionItem,
  type RequestDetail,
  type RequestImage,
  ROLE_ORDER,
  type RoleName,
} from "../api/apiTypes.ts";
import { formatBytes, formatSeconds } from "../format/formatValues.ts";
import { type QuestionValue, topQuestionValues } from "./topQuestionValues.ts";

/** 役割の advisor の問いの確率。value が null は値なし、accepted は advisor を呼ぶ要求を受け付けた役割 */
export type AdvisorProbabilityRow = { role: RoleName; value: number | null; accepted: boolean };

/**
 * ROLE_ORDER の順に、4 つの役割の advisor の問いの確率を返す。value は probabilities の値 (無ければ null)、
 * accepted は役割の名前が accepted (要求の advisor_accepted) に含まれるか
 */
export const advisorProbabilityRows = (
  probabilities: Partial<Record<RoleName, number>> | null,
  accepted: readonly string[],
): AdvisorProbabilityRow[] =>
  ROLE_ORDER.map((role) => ({
    role,
    value: probabilities?.[role] ?? null,
    accepted: accepted.includes(role),
  }));

/** 問いの値の行。advisor は問いの kind が "advisor" (しきい値の線を出す問い) か */
export type TopQuestionRow = QuestionValue & { advisor: boolean };

/** topQuestionValues(questions, p, limit) の各行に、問いの kind が "advisor" かを足して返す */
export const topQuestionRows = (
  questions: readonly QuestionItem[],
  p: readonly (number | null)[] | null,
  limit: number | null,
): TopQuestionRow[] =>
  topQuestionValues(questions, p, limit).map((value) => ({
    ...value,
    advisor: questions[value.index]?.kind === "advisor",
  }));

/** 要求に載せた画像の大きさ (bytes) の合計を返す */
export const totalImageBytes = (images: readonly Pick<RequestImage, "bytes">[]): number =>
  images.reduce((total, image) => total + image.bytes, 0);

/** 「判断」の行に出す 3 つの文 */
export type DecisionFacts = { decide: string; images: string; bytes: string };

/**
 * 要求の詳細から「判断」の行の文を返す。decide は判断の所要時間 "<秒 (小数 3 桁)> 秒" (decide_s が null なら
 * "—")、images は "画像 <枚数> 枚"、bytes は画像の大きさの合計 (formatBytes)
 */
export const decisionFacts = (detail: Pick<RequestDetail, "timings" | "images">): DecisionFacts => {
  const decideS = detail.timings.decide_s;
  return {
    decide: decideS === null ? formatSeconds(null) : `${formatSeconds(decideS, 3)} 秒`,
    images: `画像 ${detail.images.length.toLocaleString("ja-JP")} 枚`,
    bytes: formatBytes(totalImageBytes(detail.images)),
  };
};
