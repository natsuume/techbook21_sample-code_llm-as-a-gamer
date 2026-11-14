// run.jsonl のイベントから、タイムラインのイベントのレーンに描く印 (点と区間) を作る純粋な関数。
import type { EventItem } from "../api/apiTypes.ts";

/**
 * イベントのレーンの印。decision_error (Decision Model の失敗) と capture_stopped (キャプチャーの停止) は点、
 * bridge_wait (ブリッジの復旧待ち) は区間。open は閉じる行が無い区間
 */
export type EventMark =
  | { kind: "decision_error" | "capture_stopped"; t: number; error: string | null }
  | { kind: "bridge_wait"; start: number; end: number; open: boolean; waitedS: number | null };

/** 点の印にする event の名前 */
const POINT_KINDS: readonly ("decision_error" | "capture_stopped")[] = [
  "decision_error",
  "capture_stopped",
];

/** event の名前が点の印の種類なら、その種類を返す (それ以外は null) */
const pointKindOf = (eventName: string): "decision_error" | "capture_stopped" | null =>
  POINT_KINDS.find((kind) => kind === eventName) ?? null;

/**
 * イベントを t の昇順 (同じ t は配列の順) に並べ直し、印を作って印の時刻 (点は t、区間は start) の昇順で返す。
 * - decision_error・capture_stopped は点。error は文字列ならその値、それ以外は null
 * - bridge_wait で区間を開き、その後の最初の bridge_wait_end で閉じる (end はその t、waitedS はその waited_s が
 *   数なら値、それ以外は null、open は false)。閉じる行が無ければ end = tEnd、open は true、waitedS は null
 * - 開いている間の bridge_wait と、開いていない bridge_wait_end は無視する。ほかの event は入れない
 */
export const buildEventMarks = (events: readonly EventItem[], tEnd: number): EventMark[] => {
  // Array.prototype.sort は安定なため、同じ t のイベントは配列の順のまま残る
  const sortedEvents = [...events].sort((left, right) => left.t - right.t);
  const marks: EventMark[] = [];
  // 開いている復旧待ちの区間の、marks の中の位置 (開いていなければ null)。
  // 区間は開いた時点の位置に置くため、marks は印の時刻の昇順のまま作られる
  let openWaitIndex: number | null = null;
  for (const event of sortedEvents) {
    const pointKind = pointKindOf(event.event);
    if (pointKind !== null) {
      marks.push({
        kind: pointKind,
        t: event.t,
        error: typeof event.error === "string" ? event.error : null,
      });
    } else if (event.event === "bridge_wait" && openWaitIndex === null) {
      openWaitIndex = marks.length;
      marks.push({ kind: "bridge_wait", start: event.t, end: tEnd, open: true, waitedS: null });
    } else if (event.event === "bridge_wait_end" && openWaitIndex !== null) {
      const openMark = marks[openWaitIndex];
      if (openMark?.kind === "bridge_wait") {
        marks[openWaitIndex] = {
          ...openMark,
          end: event.t,
          open: false,
          waitedS: typeof event.waited_s === "number" ? event.waited_s : null,
        };
      }
      openWaitIndex = null;
    }
  }
  return marks;
};
