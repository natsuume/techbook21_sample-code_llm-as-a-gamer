// タイムラインに出す文 (表示中の区間、カーソルを合わせた値の 1 行、棒・印の説明、フレームの区間のまとめ) を作る純粋な関数。
import type { FrameItem, RequestItem } from "../api/apiTypes.ts";
import type { ButtonSpan, StickSpan } from "../controller/controllerTimeline.ts";
import type { EventMark } from "../events/eventMarks.ts";
import { formatProbability } from "../format/formatProbability.ts";
import { formatSeconds } from "../format/formatValues.ts";
import type { FrameRangeSummary } from "../frames/frameMetrics.ts";
import type { NoteRewrite } from "../notes/noteHistory.ts";
import type { AdvisorSpan } from "./advisorRows.ts";
import { BIN_SECONDS, type BinCell } from "./halfSecondBins.ts";
import type { TimeRange } from "./viewWindow.ts";

/** カーソルを合わせる前の、値の 1 行の文 */
export const DEFAULT_READOUT = "帯・ヒートマップ・折れ線にカーソルを合わせると、ここに値を出します";

const LOCALE = "ja-JP";

/** 数を小数 digits 桁の ja-JP の書式にする */
const formatFixed = (value: number, digits: number): string =>
  value.toLocaleString(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits });

/** 経過秒の区間 "t=<from>–<to>" (秒は小数 digits 桁) */
const timeSpanText = (from: number, to: number, digits: number): string =>
  `t=${formatSeconds(from, digits)}–${formatSeconds(to, digits)}`;

/** 表示中の区間の文 "t=<from>–<to> 秒" (小数 1 桁) */
export const describeRange = (range: TimeRange): string =>
  `${timeSpanText(range.from, range.to, 1)} 秒`;

/** 再生位置のラベル "t=<経過秒>" (小数 2 桁) */
export const playheadLabel = (position: number): string => `t=${formatSeconds(position)}`;

/** 列の区間 "t=<start>–<start + 0.5> 秒" (小数 1 桁) */
const cellSpanText = (cell: Pick<BinCell, "start">): string =>
  `${timeSpanText(cell.start, cell.start + BIN_SECONDS, 1)} 秒`;

/**
 * 操作に採った数の帯の列の文。値があれば "t=<区間> 秒 · 操作に採った数 <値> · 要求 #<owner>"、
 * 無ければ "t=<区間> 秒 · 要求なし"
 */
export const describeAdoptedCell = (cell: BinCell): string =>
  cell.value === null || cell.owner === null
    ? `${cellSpanText(cell)} · 要求なし`
    : `${cellSpanText(cell)} · 操作に採った数 ${cell.value.toLocaleString(LOCALE)} · 要求 #${cell.owner}`;

/**
 * ヒートマップのセルの文。値があれば "<問いの名前> · t=<区間> 秒 · 最大 <値 (小数 2 桁)> · 要求 #<owner>"、
 * 無ければ "<問いの名前> · t=<区間> 秒 · 要求なし"
 */
export const describeQuestionCell = (questionName: string, cell: BinCell): string =>
  cell.value === null || cell.owner === null
    ? `${questionName} · ${cellSpanText(cell)} · 要求なし`
    : `${questionName} · ${cellSpanText(cell)} · 最大 ${formatProbability(cell.value)} · 要求 #${cell.owner}`;

/**
 * 折れ線のフレームの文 "t=<経過秒> · 鮮明度 <値> · 谷とみなす値 <値> · 直前との差分 <値>"。
 * 鮮明度と谷とみなす値は小数 1 桁、差分は小数 2 桁 (null は "—")
 */
export const describeFrame = (
  frame: Pick<FrameItem, "sharpness" | "ref" | "diff"> & { t: number },
): string =>
  `t=${formatSeconds(frame.t)} · 鮮明度 ${formatFixed(frame.sharpness, 1)} · 谷とみなす値 ${formatFixed(frame.ref, 1)} · 直前との差分 ${frame.diff === null ? "—" : formatFixed(frame.diff, 2)}`;

/**
 * 表示中の区間のフレームのまとめ "この区間: 谷の確定 <closes> 回 · 残したフレーム <kept> 枚"。
 * 確定待ちの山があれば " · 確定待ちの山 t=<peak_t> (<鮮明度 (小数 1 桁)>)" を足す
 */
export const describeFrameSummary = (summary: FrameRangeSummary): string => {
  const base = `この区間: 谷の確定 ${summary.closes.toLocaleString(LOCALE)} 回 · 残したフレーム ${summary.kept.toLocaleString(LOCALE)} 枚`;
  const peak = summary.pendingPeak;
  return peak === null
    ? base
    : `${base} · 確定待ちの山 t=${formatSeconds(peak.t)} (${formatFixed(peak.sharpness, 1)})`;
};

/**
 * 要求の棒の説明 "要求 #<n> · t=<送信>–<受信> · <result> · <決めた操作>"。受信が無ければ "t=<送信>"、
 * 決めた操作が無ければ "操作なし"。advisor を呼んだ要求は " · advisor を呼んだ (<役割>, …)" を足す
 */
export const describeRequestBar = (request: RequestItem): string => {
  const time =
    request.t_received === null
      ? `t=${formatSeconds(request.t_sent)}`
      : timeSpanText(request.t_sent, request.t_received, 2);
  const advisor =
    request.advisor_accepted.length > 0
      ? ` · advisor を呼んだ (${request.advisor_accepted.join(", ")})`
      : "";
  return `要求 #${request.n} · ${time} · ${request.result} · ${request.action ?? "操作なし"}${advisor}`;
};

/** ボタンの入力の区間の説明 "<押下|短押し> <ボタン> · t=<start>–<end>" */
export const describeButtonSpan = (span: ButtonSpan): string =>
  `${span.kind === "hold" ? "押下" : "短押し"} ${span.button} · ${timeSpanText(span.start, span.end, 2)}`;

/** スティックの入力の区間の説明 "<スティック> 倒した大きさ <magnitude (小数 2 桁)> · t=<start>–<end>" */
export const describeStickSpan = (span: StickSpan): string =>
  `${span.stick} 倒した大きさ ${formatFixed(span.magnitude, 2)} · ${timeSpanText(span.start, span.end, 2)}`;

/** advisor の実行の区間の説明 "<名前> · t=<start>–<end>"。実行中は " · 実行中" を足す */
export const describeAdvisorSpan = (span: AdvisorSpan): string =>
  `${span.name} · ${timeSpanText(span.start, span.end, 2)}${span.running ? " · 実行中" : ""}`;

/**
 * 書き換えの印の説明 "<ファイル> の書き換え t=<経過秒> · <書いた advisor>"。
 * 受け持ちの advisor 以外による書き換えは、書いた advisor の代わりに "advisor 以外 (external)"
 */
export const describeRewriteMark = (rewrite: NoteRewrite): string =>
  `${rewrite.kind} の書き換え t=${formatSeconds(rewrite.t)} · ${rewrite.external ? "advisor 以外 (external)" : rewrite.by}`;

/**
 * イベントの印の説明。
 * - decision_error: "Decision Model の失敗 t=<t>" (error があれば " · <error>")
 * - capture_stopped: "キャプチャーの停止 t=<t>" (error があれば " · <error>")
 * - bridge_wait: "ブリッジの復旧待ち t=<start>–<end>"。open なら " (終わりの記録なし)"、waitedS があれば
 *   " (<waitedS> 秒)"
 */
export const describeEventMark = (mark: EventMark): string => {
  if (mark.kind === "bridge_wait") {
    const suffix = mark.open
      ? " (終わりの記録なし)"
      : mark.waitedS === null
        ? ""
        : ` (${formatSeconds(mark.waitedS)} 秒)`;
    return `ブリッジの復旧待ち ${timeSpanText(mark.start, mark.end, 2)}${suffix}`;
  }
  const name = mark.kind === "decision_error" ? "Decision Model の失敗" : "キャプチャーの停止";
  return `${name} t=${formatSeconds(mark.t)}${mark.error === null ? "" : ` · ${mark.error}`}`;
};
