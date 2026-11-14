// コントローラー入力の時間の変化を、pad の記録 (送ったレポート) か要求の決めた操作から組み立てる純粋な関数。
// 再生位置の時刻の入力 (snapshot) と、タイムラインの入力のレーンに描く区間 (spans) を返す。
import type { EventItem, PadItem, RequestItem } from "../api/apiTypes.ts";
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
 * - 行 i のボタン b は、使う行のうち行 i より後で b が最初に状態から消える行の source が tap_release なら tap、
 *   それ以外 (apply・neutral・stop_output の行で消える、または最後まで消えない) なら hold。第4章の短押しは
 *   apply か tap_start の行で始まり、後の apply をまたいでも、離す時刻の tap_release の行で消える。押下のボタンは
 *   tap_release の行では消えない
 * - スティックは押下として扱い、{ x: 0, y: 0 } は入れない
 */
export const controllerAtFromPad: (pad: readonly PadItem[], t: number) => ControllerSnapshot =
  () => {
    throw new Error("not implemented");
  };

/**
 * 選んだ要求の操作を pad の記録で示す時刻を返す (上の段のコントローラー入力に使う)。
 * 使う行 (controllerAtFromPad と同じ選び方) のうち、source が apply で position <= t < nextRequestTime の最初の
 * 行の t を返す (nextRequestTime が null なら上限を設けない)。該当する行が無ければ position を返す。
 * 第4章は要求の応答を受けた後に操作を送り、pad の行の時刻は送信が戻った後の時刻のため、再生位置 (要求の受信時刻)
 * では pad の記録にまだ前の操作が出ている。position は選んだ要求の再生位置、nextRequestTime は次の要求の再生位置
 */
export const padSnapshotTime: (
  pad: readonly PadItem[],
  position: number,
  nextRequestTime: number | null,
) => number = () => {
  throw new Error("not implemented");
};

/**
 * 第4章が中立のレポートを送る前後に run.jsonl に書く出来事。判断の失敗・キャプチャーの停止・接続の復旧待ちは
 * 中立に戻した直後に書く。割り込み (interrupted) は書いた直後に中立に戻し、接続の失敗 (pipeline_error) は
 * 書いた後に判断のループを抜け、終了処理で中立に戻す
 */
export const NEUTRAL_EVENTS: readonly string[] = [
  "decision_error",
  "capture_stopped",
  "bridge_wait",
  "interrupted",
  "pipeline_error",
];

/**
 * events から、第4章が中立に戻した時刻 (event が NEUTRAL_EVENTS の行の t) を昇順で返す。
 * 要求の記録は中立に戻した時刻を持たないため、要求から入力を組み立てるときにこの時刻を使う
 */
export const neutralTimesFromEvents: (events: readonly EventItem[]) => number[] = () => {
  throw new Error("not implemented");
};

/**
 * 要求の決めた操作から、時刻 t のコントローラー入力を返す (source は "requests")。
 * - 使う要求は result === "ok"、t_received !== null、parseOperation(action) !== null のもの。ほかの要求は飛ばす
 * - neutralTimes は中立に戻した時刻 (neutralTimesFromEvents の結果。昇順)
 * - 使う要求 i の押下とスティックは、受信時刻 tr_i から、その後で最初に来る「次の使う要求の受信時刻」か
 *   「tr_i 以上の neutralTimes の時刻」の前まで効く。短押しは、tr_i + TAP_SECONDS と、tr_i 以上の最初の
 *   neutralTimes の時刻の、早いほうの前まで効く (第4章の中立は短押しも消す。次の要求では消えない)
 * - 中立に戻す時刻が tr_i と同じなら、要求 i の押下・スティック・短押しは効かない (サーバーは run.jsonl の時刻を
 *   decision の行の受信時刻を起点に求めるため、要求 i の後に同じミリ秒で書いた出来事は tr_i と同じ時刻になる)
 * - 同じボタンが押下と短押しの両方にあれば短押しとして扱う
 * - events の時刻は run.jsonl の壁時計から求めるため、要求の時刻と数十ミリ秒ずれることがある
 */
export const controllerAtFromRequests: (
  requests: readonly RequestItem[],
  neutralTimes: readonly number[],
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
 * 要求の選び方・中立に戻す時刻・効く区間・短押しの扱いは controllerAtFromRequests と同じ。最後の使う要求の押下は、
 * その後に中立に戻す時刻が無ければ tEnd まで効く。
 * つなぎ方と並びは inputSpansFromPad と同じ
 */
export const inputSpansFromRequests: (
  requests: readonly RequestItem[],
  neutralTimes: readonly number[],
  range: TimeRange,
  tEnd: number,
) => InputSpans = () => {
  throw new Error("not implemented");
};
