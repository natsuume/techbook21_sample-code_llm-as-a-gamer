// questionAnswers のテスト。問いの答えの行の順と件数、積み上げ棒の区切り、採った答えを確かめる
import { describe, expect, it } from "vitest";
import type { QuestionItem } from "../api/apiTypes.ts";
import {
  answerNoteOf,
  answerNoteText,
  breakableQuestionName,
  choiceSegments,
  describeChoiceSegments,
  questionAnswerRows,
} from "./questionAnswers.ts";

const buttonQuestion = (name: string): QuestionItem => ({
  name,
  text: name,
  kind: "button",
  answer_type: "choice",
  choices: [
    { name: "none", description: "" },
    { name: "hold", description: "" },
    { name: "tap", description: "" },
  ],
});

const stickQuestion = (name: string): QuestionItem => ({
  name,
  text: name,
  kind: "stick_direction",
  answer_type: "choice",
  choices: [
    { name: "none", description: "" },
    { name: "left", description: "" },
    { name: "right", description: "" },
  ],
});

const probabilityQuestion = (name: string, kind: QuestionItem["kind"]): QuestionItem => ({
  name,
  text: name,
  kind,
  answer_type: "probability",
  choices: [],
});

describe("choiceSegments", () => {
  it("none 以外を選択肢の順に左から積み、none を右端に置く", () => {
    expect(
      choiceSegments(["none", "hold", "tap"], { none: 0.25, hold: 0.5, tap: 0.25 }, "hold"),
    ).toEqual([
      { name: "hold", value: 0.5, start: 0, width: 0.5, tone: "hold", adopted: true },
      { name: "tap", value: 0.25, start: 0.5, width: 0.25, tone: "tap", adopted: false },
      { name: "none", value: 0.25, start: 0.75, width: 0.25, tone: "none", adopted: false },
    ]);
  });

  it("スティックの方向は、none 以外の 1 つ目を directionA、2 つ目を directionB にする", () => {
    expect(
      choiceSegments(["none", "left", "right"], { none: 0.5, left: 0.125, right: 0.375 }, null).map(
        (segment) => segment.tone,
      ),
    ).toEqual(["directionA", "directionB", "none"]);
  });

  it("数でない確率は 0、合計が 1 を超えた分は棒の長さで切り詰める", () => {
    const segments = choiceSegments(
      ["none", "hold", "tap"],
      { hold: 0.75, tap: 0.5, none: Number.NaN },
      undefined,
    );
    expect(segments.map((segment) => segment.value)).toEqual([0.75, 0.5, 0]);
    expect(segments.map((segment) => segment.width)).toEqual([0.75, 0.25, 0]);
  });

  it("選択肢の名前が無ければ、確率のキーの順を使う", () => {
    expect(choiceSegments([], { none: 0.5, up: 0.5 }, "up").map((segment) => segment.name)).toEqual(
      ["up", "none"],
    );
  });
});

describe("answerNoteOf", () => {
  it("adopted の値から採った答えを返す", () => {
    expect(answerNoteOf("tap")).toEqual({ kind: "adopted", choice: "tap" });
    expect(answerNoteOf("rejected")).toEqual({ kind: "rejected" });
    expect(answerNoteOf("requested")).toEqual({ kind: "requested" });
    expect(answerNoteOf(null)).toBeNull();
    expect(answerNoteOf(undefined)).toBeNull();
  });

  it("文は 採用 <選択肢>・不採用・requested", () => {
    expect(answerNoteText({ kind: "adopted", choice: "right" })).toBe("採用 right");
    expect(answerNoteText({ kind: "rejected" })).toBe("不採用");
    expect(answerNoteText({ kind: "requested" })).toBe("requested");
  });
});

describe("questionAnswerRows", () => {
  const questions = [
    buttonQuestion("button_A"),
    stickQuestion("stick_LS_x"),
    probabilityQuestion("stick_LS_x_at_least_50", "stick_strength"),
    probabilityQuestion("advisor_tips", "advisor"),
  ];
  const answers = {
    p: [0.75, 0.25, null, 0.5],
    choices: {
      button_A: { none: 0.25, hold: 0.5, tap: 0.25 },
      stick_LS_x: { none: 0.75, left: 0, right: 0.25 },
    },
    adopted: { button_A: "hold" as const, advisor_tips: null, stick_LS_x: null },
  };
  const options = { limit: null, focusQuestion: null, advisorThreshold: 0.6 };

  it("問いの値の降順に並べ、null の値の問いを除く", () => {
    expect(questionAnswerRows(questions, answers, options).map((row) => row.name)).toEqual([
      "button_A",
      "advisor_tips",
      "stick_LS_x",
    ]);
  });

  it("選択肢の問いは区切り、advisor の問いはしきい値、採った答えを付ける", () => {
    const rows = questionAnswerRows(questions, answers, options);
    expect(rows[0]).toMatchObject({
      index: 0,
      kind: "button",
      value: 0.75,
      threshold: null,
      note: { kind: "adopted", choice: "hold" },
      focused: false,
    });
    expect(rows[0]?.segments?.map((segment) => segment.name)).toEqual(["hold", "tap", "none"]);
    expect(rows[1]).toMatchObject({ name: "advisor_tips", segments: null, threshold: 0.6 });
    expect(rows[1]?.note).toBeNull();
  });

  it("limit 件に切り、上位に入らない focusQuestion の問いを最後に足して強調する", () => {
    const rows = questionAnswerRows(questions, answers, {
      ...options,
      limit: 1,
      focusQuestion: "stick_LS_x",
    });
    expect(rows.map((row) => [row.name, row.focused])).toEqual([
      ["button_A", false],
      ["stick_LS_x", true],
    ]);
  });

  it("上位に入る focusQuestion の問いは、その位置のまま強調する", () => {
    const rows = questionAnswerRows(questions, answers, {
      ...options,
      limit: 2,
      focusQuestion: "advisor_tips",
    });
    expect(rows.map((row) => [row.name, row.focused])).toEqual([
      ["button_A", false],
      ["advisor_tips", true],
    ]);
  });

  it("p が null なら []", () => {
    expect(questionAnswerRows(questions, { ...answers, p: null }, options)).toEqual([]);
  });

  it("選択肢の確率が無い選択肢の問いは、区切りを null にする", () => {
    const rows = questionAnswerRows(questions, { ...answers, choices: null }, options);
    expect(rows[0]?.segments).toBeNull();
  });
});

describe("breakableQuestionName", () => {
  it("_ の後に幅の無い空白を入れる", () => {
    expect(breakableQuestionName("stick_LS_x")).toBe("stick_​LS_​x");
    expect(breakableQuestionName("advisor")).toBe("advisor");
  });
});

describe("describeChoiceSegments", () => {
  it("区切りの順に、選択肢と確率をつなぐ", () => {
    expect(
      describeChoiceSegments(
        choiceSegments(["none", "hold", "tap"], { none: 0.25, hold: 0.5, tap: 0.25 }, null),
      ),
    ).toBe("hold 0.50 · tap 0.25 · none 0.25");
  });
});
