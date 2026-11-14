// 問いの値のヒートマップの行の並び (問いを kind ごとにまとめた行の y と高さ) と、カーソルの y から行を引く純粋な関数。
import type { QuestionItem, QuestionKind } from "../api/apiTypes.ts";

/** 問い 1 つの行の高さ (px) */
export const QUESTION_ROW_HEIGHT = 2;
/** advisor の問いの行の高さ (px) */
export const ADVISOR_ROW_HEIGHT = 8;
/** kind のまとまりの間の高さ (px) */
export const GROUP_GAP = 3;
/** まとまりを並べる順 */
export const QUESTION_KIND_ORDER: readonly QuestionKind[] = [
  "button",
  "stick_direction",
  "stick_strength",
  "advisor",
];

/** ヒートマップの 1 行。questionIndex は summary.questions (要求の p) の中の位置。y は上からの位置 (px) */
export type HeatmapRow = {
  questionIndex: number;
  name: string;
  kind: QuestionKind;
  y: number;
  height: number;
};
/** kind のまとまり。label は "<kind> <問いの数>" (例 "button 17") */
export type HeatmapGroup = { kind: QuestionKind; label: string; y: number; height: number };
/** ヒートマップの行の並び。height は全体の高さ (px) */
export type HeatmapLayout = { rows: HeatmapRow[]; groups: HeatmapGroup[]; height: number };

/**
 * 問いを QUESTION_KIND_ORDER の順のまとまりにし (まとまりの中は questions の順)、上から行を並べる。
 * 行の高さは advisor の問いが 8、ほかが 2。問いの無い kind のまとまりは作らず、まとまりの間に 3 を空ける
 */
export const heatmapLayout = (
  questions: readonly Pick<QuestionItem, "name" | "kind">[],
): HeatmapLayout => {
  const rows: HeatmapRow[] = [];
  const groups: HeatmapGroup[] = [];
  let y = 0;
  for (const kind of QUESTION_KIND_ORDER) {
    const members = questions
      .map((question, questionIndex) => ({ question, questionIndex }))
      .filter(({ question }) => question.kind === kind);
    if (members.length === 0) {
      continue;
    }
    if (groups.length > 0) {
      y += GROUP_GAP;
    }
    const groupY = y;
    const height = kind === "advisor" ? ADVISOR_ROW_HEIGHT : QUESTION_ROW_HEIGHT;
    for (const { question, questionIndex } of members) {
      rows.push({ questionIndex, name: question.name, kind, y, height });
      y += height;
    }
    groups.push({ kind, label: `${kind} ${members.length}`, y: groupY, height: y - groupY });
  }
  return { rows, groups, height: y };
};

/** 上からの位置 y (px) の行を返す。まとまりの間と、行の外は null */
export const heatmapRowAt = (layout: HeatmapLayout, y: number): HeatmapRow | null =>
  layout.rows.find((row) => y >= row.y && y < row.y + row.height) ?? null;
