// タイムラインのコントローラー入力のレーンの 5 行 (十字キー / A B X Y / L R ZL ZR / + − HOME / スティック) に、
// 入力の区間を振り分ける純粋な関数。
import type { ButtonSpan, InputSpans, StickSpan } from "../controller/controllerTimeline.ts";
import type { ButtonName, StickName } from "../controller/operationText.ts";

/** 入力のレーンの 1 行。buttons と sticks はその行に描くボタンとスティック */
export type ControllerRow = {
  label: string;
  buttons: readonly ButtonName[];
  sticks: readonly StickName[];
};

/**
 * 入力のレーンの行。スティックの行には、スティックの倒し方と、スティックの押し込み (L3・R3) を描く
 */
export const CONTROLLER_ROWS: readonly ControllerRow[] = [
  { label: "十字キー", buttons: ["UP", "DOWN", "LEFT", "RIGHT"], sticks: [] },
  { label: "A B X Y", buttons: ["A", "B", "X", "Y"], sticks: [] },
  { label: "L R ZL ZR", buttons: ["L", "R", "ZL", "ZR"], sticks: [] },
  { label: "+ − HOME", buttons: ["PLUS", "MINUS", "HOME"], sticks: [] },
  { label: "スティック", buttons: ["L3", "R3"], sticks: ["LS", "RS"] },
];

/** 1 行に描く入力の区間 */
export type ControllerRowSpans = { row: ControllerRow; buttons: ButtonSpan[]; sticks: StickSpan[] };

/** 入力の区間を CONTROLLER_ROWS の行に振り分ける。行の中の区間の並びは spans の順のまま */
export const splitInputSpans = (spans: InputSpans): ControllerRowSpans[] =>
  CONTROLLER_ROWS.map((row) => ({
    row,
    buttons: spans.buttons.filter((span) => row.buttons.includes(span.button)),
    sticks: spans.sticks.filter((span) => row.sticks.includes(span.stick)),
  }));
