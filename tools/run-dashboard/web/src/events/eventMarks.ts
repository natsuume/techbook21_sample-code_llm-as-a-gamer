// run.jsonl のイベントから、タイムラインのイベントのレーンに描く印 (点と区間) を作る純粋な関数。
import type { EventItem } from "../api/apiTypes.ts";

/**
 * イベントのレーンの印。decision_error (Decision Model の失敗) と capture_stopped (キャプチャーの停止) は点、
 * bridge_wait (ブリッジの復旧待ち) は区間。open は閉じる行が無い区間
 */
export type EventMark =
  | { kind: "decision_error" | "capture_stopped"; t: number; error: string | null }
  | { kind: "bridge_wait"; start: number; end: number; open: boolean; waitedS: number | null };

/**
 * イベントを t の昇順 (同じ t は配列の順) に並べ直し、印を作って印の時刻 (点は t、区間は start) の昇順で返す。
 * - decision_error・capture_stopped は点。error は文字列ならその値、それ以外は null
 * - bridge_wait で区間を開き、その後の最初の bridge_wait_end で閉じる (end はその t、waitedS はその waited_s が
 *   数なら値、それ以外は null、open は false)。閉じる行が無ければ end = tEnd、open は true、waitedS は null
 * - 開いている間の bridge_wait と、開いていない bridge_wait_end は無視する。ほかの event は入れない
 */
export const buildEventMarks: (events: readonly EventItem[], tEnd: number) => EventMark[] = () => {
  throw new Error("not implemented");
};
