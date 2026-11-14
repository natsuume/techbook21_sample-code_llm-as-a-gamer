// selectedRequestSummary のテスト。advisor の問いの確率の行、問いの値の上位の行、画像の大きさの合計を確かめる
import { describe, expect, it } from "vitest";
import type { QuestionItem } from "../api/apiTypes.ts";
import {
  advisorProbabilityRows,
  decisionFacts,
  topQuestionRows,
  totalImageBytes,
} from "./selectedRequestSummary.ts";

const question = (name: string, kind: QuestionItem["kind"]): QuestionItem => ({
  name,
  text: name,
  kind,
  answer_type: kind === "button" ? "choice" : "probability",
  choices: [],
});

describe("advisorProbabilityRows", () => {
  it("ROLE_ORDER の順に、確率と受け付けたかを返す", () => {
    expect(advisorProbabilityRows({ strategy: 0.875, tips: 0.6 }, ["strategy"])).toEqual([
      { role: "controls", value: null, accepted: false },
      { role: "strategy", value: 0.875, accepted: true },
      { role: "advice", value: null, accepted: false },
      { role: "tips", value: 0.6, accepted: false },
    ]);
  });

  it("確率が null なら、どの役割も値なし", () => {
    expect(advisorProbabilityRows(null, []).map((row) => row.value)).toEqual([
      null,
      null,
      null,
      null,
    ]);
  });
});

describe("topQuestionRows", () => {
  const questions = [
    question("button_A", "button"),
    question("advisor_tips", "advisor"),
    question("stick_LS_x", "stick_direction"),
  ];

  it("値の大きい問いを limit 件返し、advisor の問いかを付ける", () => {
    expect(topQuestionRows(questions, [0.5, 0.75, 0.25], 2)).toEqual([
      { index: 1, name: "advisor_tips", value: 0.75, advisor: true },
      { index: 0, name: "button_A", value: 0.5, advisor: false },
    ]);
  });

  it("p が null なら []", () => {
    expect(topQuestionRows(questions, null, 3)).toEqual([]);
  });
});

describe("totalImageBytes", () => {
  it("画像の大きさを足す", () => {
    expect(totalImageBytes([{ bytes: 641 }, { bytes: 1024 }])).toBe(1665);
  });

  it("画像が無ければ 0", () => {
    expect(totalImageBytes([])).toBe(0);
  });
});

describe("decisionFacts", () => {
  const image = (bytes: number) => ({ label: "最新", t: 1, bytes, thumb: null, file: null });
  const timings = (decideS: number | null) => ({
    select_s: null,
    encode_s: null,
    decide_s: decideS,
    apply_s: null,
  });

  it("判断の所要時間・画像の枚数・大きさの合計の文を返す", () => {
    expect(decisionFacts({ timings: timings(0.125), images: [image(641), image(1024)] })).toEqual({
      decide: "0.125 秒",
      images: "画像 2 枚",
      bytes: "1.6 KB",
    });
  });

  it("判断の所要時間が null なら単位を付けずに「—」にする", () => {
    expect(decisionFacts({ timings: timings(null), images: [] })).toEqual({
      decide: "—",
      images: "画像 0 枚",
      bytes: "0 B",
    });
  });
});
