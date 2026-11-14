// コントローラー入力の時間の変化を、pad の記録 (送ったレポート) か要求の決めた操作から組み立てる純粋な関数。
// 再生位置の時刻の入力 (snapshot) と、タイムラインの入力のレーンに描く区間 (spans) を返す。
import type { PadItem, RequestItem } from "../api/apiTypes.ts";
import type { TimeRange } from "../timeline/viewWindow.ts";
import type { ButtonName, StickName, StickValue } from "./operationText.ts";

/** 要求の操作の短押しを押している秒数 */
export const TAP_SECONDS = 0.12;
/** 押下 (hold) か短押し (tap) か */
export type InputKind = "hold" | "tap";
/** ある時刻のコントローラー入力。source は組み立てに使った記録 */
export type ControllerSnapshot = {
  buttons: { name: ButtonName; kind: InputKind }[];
  sticks: Partial<Record<StickName, StickValue>>;
  source: "pad" | "requests";
};
/** ボタンを押している区間 [start, end) */
export type ButtonSpan = { button: ButtonName; kind: InputKind; start: number; end: number };
/** スティックを倒している区間 [start, end)。magnitude は倒した大きさ (0〜1) */
export type StickSpan = { stick: StickName; start: number; end: number; magnitude: number };
export type InputSpans = { buttons: ButtonSpan[]; sticks: StickSpan[]; source: "pad" | "requests" };

/**
 * pad の記録から、時刻 t のコントローラー入力を返す (source は "pad")。
 * - 使う行は ok === true、t !== null、parsePadState(state) !== null のもの。t の昇順 (同じ t は配列の順)
 * - 行 i の状態は [t_i, t_{i+1}) に効く。最初の行より前は何も押していない
 * - 行 i の source が apply か tap_start で、次の行 i+1 の source が tap_release のとき、行 i のボタンのうち
 *   行 i+1 の状態に無いものが tap、ほかは hold。それ以外の行のボタンはすべて hold
 * - スティックは押下として扱い、{ x: 0, y: 0 } は入れない
 */
export const controllerAtFromPad: (pad: readonly PadItem[], t: number) => ControllerSnapshot =
  () => {
    throw new Error("not implemented");
  };

/**
 * 要求の決めた操作から、時刻 t のコントローラー入力を返す (source は "requests")。
 * - 使う要求は result === "ok"、t_received !== null、parseOperation(action) !== null のもの (配列の順)
 * - 要求 i の押下とスティックは [tr_i, tr_{i+1}) (tr は t_received。次は次の使う要求)、
 *   短押しは [tr_i, tr_i + TAP_SECONDS) に効く
 * - 同じボタンが押下と短押しの両方にあれば短押しとして扱う
 */
export const controllerAtFromRequests: (
  requests: readonly RequestItem[],
  t: number,
) => ControllerSnapshot = () => {
  throw new Error("not implemented");
};

/**
 * pad の記録から、range と重なる入力の区間を range に切り詰めて返す (source は "pad")。
 * 行の選び方・効く区間・tap と hold の区別は controllerAtFromPad と同じ。最後の行は tEnd まで効く。
 * スティックの magnitude は min(1, hypot(x, y))。
 * 隣り合う同じボタン・同じ kind の区間 (前の end === 次の start) は 1 つにつなぐ。スティックは同じ magnitude の
 * ときだけつなぐ。並びは start の昇順、同じ start はボタンの BUTTON_NAMES の順
 */
export const inputSpansFromPad: (
  pad: readonly PadItem[],
  range: TimeRange,
  tEnd: number,
) => InputSpans = () => {
  throw new Error("not implemented");
};

/**
 * 要求の決めた操作から、range と重なる入力の区間を range に切り詰めて返す (source は "requests")。
 * 要求の選び方・効く区間・短押しの扱いは controllerAtFromRequests と同じ。最後の使う要求の押下は tEnd まで効く。
 * つなぎ方と並びは inputSpansFromPad と同じ
 */
export const inputSpansFromRequests: (
  requests: readonly RequestItem[],
  range: TimeRange,
  tEnd: number,
) => InputSpans = () => {
  throw new Error("not implemented");
};
