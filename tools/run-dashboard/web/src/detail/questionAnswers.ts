// 詳細ペインの要求のタブの「問いの答え」の行 (問いの値の順、選択肢の積み上げ棒の区切り、採った答え) を求める純粋な関数。
import type { AdoptedValue, QuestionItem, QuestionKind, RequestDetail } from "../api/apiTypes.ts";
import { formatProbability } from "../format/formatProbability.ts";
import { topQuestionValues } from "../requests/topQuestionValues.ts";

/** 問いの答えを、問いの値の上位から出す件数 (すべてを出さないとき) */
export const TOP_ANSWER_COUNT = 8;

/** 選択肢の none (押さない・倒さない) の名前 */
const NONE_CHOICE = "none";

/**
 * 積み上げ棒の区切りの色の種類。none は地のまま、hold は押下、tap は短押し、
 * directionA・directionB は none・hold・tap 以外の選択肢 (スティックの方向など) の 1 つ目と 2 つ目以降
 */
export type ChoiceTone = "none" | "hold" | "tap" | "directionA" | "directionB";

/** 選択肢の積み上げ棒の 1 区切り。start と width は棒の長さに対する 0〜1 の割合 */
export type ChoiceSegment = {
  name: string;
  value: number;
  start: number;
  width: number;
  tone: ChoiceTone;
  /** adopted の値がこの選択肢か */
  adopted: boolean;
};

/**
 * 問いの採った答え。adopted は採った選択肢、rejected は不採用 (十字キーの反対の方向に負けた)、
 * requested は advisor に求めた (advisor の問いの確率がしきい値を超えた)
 */
export type AnswerNote =
  | { kind: "adopted"; choice: string }
  | { kind: "rejected" }
  | { kind: "requested" };

/** 問いの答えの 1 行 */
export type QuestionAnswerRow = {
  /** summary.questions (と p) の中の位置 */
  index: number;
  name: string;
  kind: QuestionKind;
  /** 問いの値 (p の値) */
  value: number;
  /** 選択肢の問いで、選択肢ごとの確率があれば積み上げ棒の区切り。無ければ null (値の棒で出す) */
  segments: ChoiceSegment[] | null;
  /** advisor の問いのしきい値。advisor の問いでなければ null */
  threshold: number | null;
  note: AnswerNote | null;
  /** focusQuestion の問いか */
  focused: boolean;
};

/** questionAnswerRows に渡す、要求の詳細の答え */
export type RequestAnswers = Pick<RequestDetail, "p" | "choices" | "adopted">;

export type QuestionAnswerOptions = {
  /** 問いの値の上位から出す件数。null は全件 */
  limit: number | null;
  /** 枠で強調する問いの名前 (ViewState の focusQuestion) */
  focusQuestion: string | null;
  /** advisor の問いのしきい値 (summary.thresholds.advisor) */
  advisorThreshold: number;
};

/** 0〜1 の範囲に切り詰める (数でない値は 0) */
const clampRatio = (value: number): number =>
  Number.isFinite(value) ? Math.min(Math.max(value, 0), 1) : 0;

/** 選択肢の区切りの色の種類。none・hold・tap 以外は、none 以外の選択肢の中の順 (0 始まり) で決める */
const toneOf = (name: string, orderAmongOthers: number): ChoiceTone => {
  if (name === NONE_CHOICE) {
    return "none";
  }
  if (name === "hold" || name === "tap") {
    return name;
  }
  return orderAmongOthers === 0 ? "directionA" : "directionB";
};

/**
 * 選択肢の問いの積み上げ棒の区切りを返す。choiceNames は問いの選択肢の名前 (空なら probabilities のキーの順)。
 * none 以外の選択肢を choiceNames の順に左から積み、none を最後 (右端) に置く。そのため none 以外の区切りの合計の長さが
 * 問いの値 (none 以外の確率の合計) になる。数でない確率は 0、区切りの終わりは棒の長さ (1) で切り詰める
 */
export const choiceSegments = (
  choiceNames: readonly string[],
  probabilities: Readonly<Record<string, number>>,
  adopted: AdoptedValue | undefined,
): ChoiceSegment[] => {
  const names = choiceNames.length > 0 ? choiceNames : Object.keys(probabilities);
  const ordered = [
    ...names.filter((name) => name !== NONE_CHOICE),
    ...names.filter((name) => name === NONE_CHOICE),
  ];
  let start = 0;
  let orderAmongOthers = 0;
  return ordered.map((name) => {
    const raw = probabilities[name];
    const value = typeof raw === "number" && Number.isFinite(raw) ? raw : 0;
    const width = Math.max(0, Math.min(clampRatio(value), 1 - start));
    const tone = toneOf(name, orderAmongOthers);
    if (tone === "directionA" || tone === "directionB") {
      orderAmongOthers += 1;
    }
    const segment: ChoiceSegment = {
      name,
      value,
      start,
      width,
      tone,
      adopted: adopted === name,
    };
    start += width;
    return segment;
  });
};

/** adopted の値から採った答えを返す。null・undefined は null */
export const answerNoteOf = (adopted: AdoptedValue | undefined): AnswerNote | null => {
  if (adopted === null || adopted === undefined) {
    return null;
  }
  if (adopted === "rejected") {
    return { kind: "rejected" };
  }
  if (adopted === "requested") {
    return { kind: "requested" };
  }
  return { kind: "adopted", choice: adopted };
};

/**
 * 問いの答えの行を返す。
 * - 行の順と件数は topQuestionValues(questions, answers.p, limit) (値の降順、null の値は除く)。p が null なら []
 * - focusQuestion の問いが上位に入らず、値があれば、最後に足す
 * - 選択肢の問い (answer_type が "choice") で answers.choices[name] があれば segments (choiceSegments)
 * - advisor の問いは threshold に advisorThreshold を入れる
 * - note は answers.adopted[name] の answerNoteOf
 */
export const questionAnswerRows = (
  questions: readonly QuestionItem[],
  answers: RequestAnswers,
  options: QuestionAnswerOptions,
): QuestionAnswerRow[] => {
  const values = topQuestionValues(questions, answers.p, null);
  const shown = options.limit === null ? values : values.slice(0, options.limit);
  const focused = values.find((value) => value.name === options.focusQuestion);
  const withFocus = focused === undefined || shown.includes(focused) ? shown : [...shown, focused];
  return withFocus.flatMap((value): QuestionAnswerRow[] => {
    const question = questions[value.index];
    if (question === undefined) {
      return [];
    }
    const adopted = answers.adopted?.[question.name];
    const probabilities = answers.choices?.[question.name];
    return [
      {
        index: value.index,
        name: question.name,
        kind: question.kind,
        value: value.value,
        segments:
          question.answer_type === "choice" && probabilities !== undefined
            ? choiceSegments(
                question.choices.map((choice) => choice.name),
                probabilities,
                adopted,
              )
            : null,
        threshold: question.kind === "advisor" ? options.advisorThreshold : null,
        note: answerNoteOf(adopted),
        focused: question.name === options.focusQuestion,
      },
    ];
  });
};

/** 不採用の理由 (title と、画面に出さない文で示す) */
export const REJECTED_REASON = "押す側の確率が高かったが、十字キーの反対の方向に負けて採らなかった";

/** 採った答えの文。adopted は "採用 <選択肢>"、rejected は "不採用"、requested は "requested" */
export const answerNoteText = (note: AnswerNote): string => {
  switch (note.kind) {
    case "adopted":
      return `採用 ${note.choice}`;
    case "rejected":
      return "不採用";
    case "requested":
      return "requested";
  }
};

/** 幅の無い空白 (折り返してよい位置を示す) */
const ZERO_WIDTH_SPACE = "​";

/** 問いの名前の "_" の後に幅の無い空白を入れ、狭い列では "_" の後で折り返すようにする */
export const breakableQuestionName = (name: string): string =>
  name.replaceAll("_", `_${ZERO_WIDTH_SPACE}`);

/** 積み上げ棒の区切りの文 "<選択肢> <確率 (小数 2 桁)>" を、区切りの順に " · " でつなぐ */
export const describeChoiceSegments = (segments: readonly ChoiceSegment[]): string =>
  segments.map((segment) => `${segment.name} ${formatProbability(segment.value)}`).join(" · ");
