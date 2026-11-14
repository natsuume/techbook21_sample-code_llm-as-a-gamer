// 詳細ペインの要求のタブの「所要時間の内訳」(4 区間の積み上げ棒と ms の値) を求める純粋な関数。
import type { RequestDetail } from "../api/apiTypes.ts";

/** 所要時間の区間のキー (要求の詳細の timings のキー) */
export type TimingKey = keyof RequestDetail["timings"];

/** 区間の順と名前 */
export const TIMING_PARTS: readonly { key: TimingKey; label: string }[] = [
  { key: "select_s", label: "選択" },
  { key: "encode_s", label: "符号化" },
  { key: "decide_s", label: "判断 (HTTP を含む)" },
  { key: "apply_s", label: "操作の送信" },
];

/** 積み上げ棒の 1 区間。start と width は合計に対する 0〜1 の割合 */
export type TimingSegment = {
  key: TimingKey;
  label: string;
  seconds: number | null;
  start: number;
  width: number;
};

/** 所要時間の内訳。totalSeconds は値のある区間の合計 (どの区間も値が無ければ null) */
export type TimingBreakdown = { segments: TimingSegment[]; totalSeconds: number | null };

/**
 * TIMING_PARTS の順に、区間の秒と積み上げ棒の位置を返す。
 * 値が null の区間は幅 0。合計が 0 以下 (どの区間も 0 か値なし) なら、すべての幅を 0 にする
 */
export const timingBreakdown = (timings: RequestDetail["timings"]): TimingBreakdown => {
  const values = TIMING_PARTS.map((part) => timings[part.key]);
  const present = values.filter((value): value is number => value !== null);
  const totalSeconds = present.length === 0 ? null : present.reduce((sum, value) => sum + value, 0);
  const total = totalSeconds ?? 0;
  let start = 0;
  const segments = TIMING_PARTS.map((part, index): TimingSegment => {
    const seconds = values[index] ?? null;
    const width = total > 0 && seconds !== null ? Math.max(seconds, 0) / total : 0;
    const segment = { key: part.key, label: part.label, seconds, start, width };
    start += width;
    return segment;
  });
  return { segments, totalSeconds };
};

/** 秒を "<ミリ秒 (小数 2 桁、ja-JP の書式)> ms" にする。null は "—" */
export const formatMilliseconds = (seconds: number | null): string =>
  seconds === null
    ? "—"
    : `${(seconds * 1000).toLocaleString("ja-JP", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })} ms`;
