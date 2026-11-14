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

/** 何も押していない状態の文 */
const NONE_TEXT = "none";
/** 状態の文の要素の区切り */
const PART_SEPARATOR = "+";
/** スティックの要素 "LS:x,y"・"RS:x,y"。値は小数表記 (先頭の +、指数表記、nan、inf は受け付けない) */
const STICK_PATTERN = /^(LS|RS):(-?[0-9]+(?:\.[0-9]+)?),(-?[0-9]+(?:\.[0-9]+)?)$/;
/** 操作の文 "押下 <状態> / 短押し <状態>" */
const OPERATION_PATTERN = /^押下 (\S+) \/ 短押し (\S+)$/;

const BUTTON_NAME_SET: ReadonlySet<string> = new Set(BUTTON_NAMES);
const isButtonName = (text: string): text is ButtonName => BUTTON_NAME_SET.has(text);

/**
 * レポートの状態の文を解釈する。書式は、ボタンを "+" でつなぎ、続けてスティックを "LS:x,y"・"RS:x,y" で
 * 並べたもの (例: "A+B+LS:0.8,0"、"LS:1,0+RS:0,-0.25")。何も無ければ "none"。
 * buttons は文の順のまま。解釈できない文 (知らないボタン名、数でない値、区切りの違い) は null
 */
export const parsePadState = (text: string): PadState | null => {
  if (text === NONE_TEXT) {
    return { buttons: [], sticks: {} };
  }
  const buttons: ButtonName[] = [];
  const sticks: Partial<Record<StickName, StickValue>> = {};
  for (const part of text.split(PART_SEPARATOR)) {
    if (isButtonName(part)) {
      if (!buttons.includes(part)) {
        buttons.push(part);
      }
      continue;
    }
    const stickMatch = STICK_PATTERN.exec(part);
    const stick = stickMatch?.[1];
    const x = stickMatch?.[2];
    const y = stickMatch?.[3];
    if (
      (stick !== "LS" && stick !== "RS") ||
      x === undefined ||
      y === undefined ||
      sticks[stick] !== undefined
    ) {
      return null;
    }
    sticks[stick] = { x: Number(x), y: Number(y) };
  }
  return { buttons, sticks };
};

/**
 * 操作の文 "押下 <状態> / 短押し <状態>" を解釈する (例: "押下 A+UP / 短押し B")。短押しはボタンだけ。
 * 解釈できない文 (知らないボタン名、数でない値、区切りの違い、短押しにスティック) は null
 */
export const parseOperation = (action: string): Operation | null => {
  const operationMatch = OPERATION_PATTERN.exec(action);
  const heldText = operationMatch?.[1];
  const tapsText = operationMatch?.[2];
  if (heldText === undefined || tapsText === undefined) {
    return null;
  }
  const held = parsePadState(heldText);
  const taps = parsePadState(tapsText);
  if (held === null || taps === null || Object.keys(taps.sticks).length > 0) {
    return null;
  }
  return { held, taps: taps.buttons };
};
