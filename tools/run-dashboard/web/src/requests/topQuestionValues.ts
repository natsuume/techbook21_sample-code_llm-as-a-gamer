// 要求の問いの値 (p) から、値の大きい問いを順に選ぶ純粋な関数。
import type { QuestionItem } from "../api/apiTypes.ts";

/** 問いの値。index は questions (と p) の中の位置 */
export type QuestionValue = { index: number; name: string; value: number };

/**
 * 問いの値を、値の降順 (同じ値は index の昇順) で最大 limit 件返す。
 * p が null なら []。null の値は除く。limit が null なら全件
 */
export const topQuestionValues = (
  questions: readonly QuestionItem[],
  p: readonly (number | null)[] | null,
  limit: number | null,
): QuestionValue[] => {
  if (p === null) {
    return [];
  }
  const values: QuestionValue[] = [];
  questions.forEach((question, index) => {
    const value = p[index];
    if (value !== null && value !== undefined) {
      values.push({ index, name: question.name, value });
    }
  });
  values.sort((left, right) => right.value - left.value || left.index - right.index);
  return limit === null ? values : values.slice(0, limit);
};
