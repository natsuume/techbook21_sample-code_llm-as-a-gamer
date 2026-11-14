// 第 4 章の記録にあるコントローラーの状態の文 (pad のレポートの state) と操作の文 (要求の action) を解釈する純粋な関数。

export const BUTTON_NAMES = [
  "A",
  "B",
  "X",
  "Y",
  "L",
  "R",
  "ZL",
  "ZR",
  "L3",
  "R3",
  "PLUS",
  "MINUS",
  "HOME",
  "UP",
  "RIGHT",
  "DOWN",
  "LEFT",
] as const;
export type ButtonName = (typeof BUTTON_NAMES)[number];
export type StickName = "LS" | "RS";
/** スティックの倒し方。x は右、y は上が正 (-1〜1) */
export type StickValue = { x: number; y: number };
export type PadState = { buttons: ButtonName[]; sticks: Partial<Record<StickName, StickValue>> };
/** 操作。held は押下 (ボタンとスティック)、taps は短押しのボタン */
export type Operation = { held: PadState; taps: ButtonName[] };

/**
 * レポートの状態の文を解釈する。書式は、ボタンを "+" でつなぎ、続けてスティックを "LS:x,y"・"RS:x,y" で
 * 並べたもの (例: "A+B+LS:0.8,0"、"LS:1,0+RS:0,-0.25")。何も無ければ "none"。
 * buttons は文の順のまま。解釈できない文 (知らないボタン名、数でない値、区切りの違い) は null
 */
export const parsePadState: (text: string) => PadState | null = () => {
  throw new Error("not implemented");
};

/**
 * 操作の文 "押下 <状態> / 短押し <状態>" を解釈する (例: "押下 A+UP / 短押し B")。短押しはボタンだけ。
 * 解釈できない文 (知らないボタン名、数でない値、区切りの違い、短押しにスティック) は null
 */
export const parseOperation: (action: string) => Operation | null = () => {
  throw new Error("not implemented");
};
