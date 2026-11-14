// topQuestionValues のテスト。値の順、同じ値の順、null の除き方、件数の上限を確かめる
import { describe, expect, it } from "vitest";
import type { QuestionItem } from "../api/apiTypes.ts";
import { topQuestionValues } from "./topQuestionValues.ts";

// テスト用の問い。名前のほかは固定の値にする
const question = (name: string): QuestionItem => ({
  name,
  text: `${name} の問い`,
  kind: "button",
  answer_type: "probability",
  choices: [],
});

const questions = [question("A"), question("B"), question("UP"), question("advisor_tips")];

describe("topQuestionValues", () => {
  it("値の降順に、問いの位置・名前・値を返す", () => {
    expect(topQuestionValues(questions, [0.1, 0.9, 0.5, 0.3], null)).toEqual([
      { index: 1, name: "B", value: 0.9 },
      { index: 2, name: "UP", value: 0.5 },
      { index: 3, name: "advisor_tips", value: 0.3 },
      { index: 0, name: "A", value: 0.1 },
    ]);
  });

  it("同じ値は index の昇順に並べる", () => {
    expect(topQuestionValues(questions, [0.4, 0.7, 0.4, 0.7], null)).toEqual([
      { index: 1, name: "B", value: 0.7 },
      { index: 3, name: "advisor_tips", value: 0.7 },
      { index: 0, name: "A", value: 0.4 },
      { index: 2, name: "UP", value: 0.4 },
    ]);
  });

  it("null の値は除く", () => {
    expect(topQuestionValues(questions, [null, 0.2, null, 0.6], null)).toEqual([
      { index: 3, name: "advisor_tips", value: 0.6 },
      { index: 1, name: "B", value: 0.2 },
    ]);
  });

  it("値が 0 の問いは除かない", () => {
    expect(topQuestionValues(questions, [0, null, null, null], null)).toEqual([
      { index: 0, name: "A", value: 0 },
    ]);
  });

  it("limit があれば、上位の limit 件だけを返す", () => {
    expect(topQuestionValues(questions, [0.1, 0.9, 0.5, 0.3], 2)).toEqual([
      { index: 1, name: "B", value: 0.9 },
      { index: 2, name: "UP", value: 0.5 },
    ]);
  });

  it("limit が値のある問いの数より大きければ、値のある問いをすべて返す", () => {
    expect(topQuestionValues(questions, [null, 0.9, null, 0.3], 3)).toEqual([
      { index: 1, name: "B", value: 0.9 },
      { index: 3, name: "advisor_tips", value: 0.3 },
    ]);
  });

  it("limit が null なら全件を返す", () => {
    expect(topQuestionValues(questions, [0.1, 0.9, 0.5, 0.3], null)).toHaveLength(4);
  });

  it("p が null なら空の配列を返す", () => {
    expect(topQuestionValues(questions, null, 3)).toEqual([]);
  });
});
