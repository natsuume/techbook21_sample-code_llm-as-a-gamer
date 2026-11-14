// 操作に採った数の帯と、問いの値のヒートマップのセルの色を決める純粋な関数。
// 色の値は design token (CSS 変数) の 6 桁の hex を、描く側が読んで渡す。
import type { QuestionKind } from "../api/apiTypes.ts";

/** 0〜255 の赤・緑・青 */
export type Rgb = { r: number; g: number; b: number };

/** ヒートマップの色。low は値 0、high は値 1 の色。hot はしきい値を超えた advisor の問いの色 (CSS の色の文字列) */
export type HeatPalette = { low: Rgb; high: Rgb; hot: string };

/** 帯の列の色の種類。zero は 0 (surface-2)、one は 1 (hot-soft)、many は 2 以上 (hot) */
export type AdoptedCountTone = "zero" | "one" | "many";

const HEX_COLOR_PATTERN = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i;

/** "#rrggbb" (前後の空白は無視、大文字と小文字を問わない) を読む。ほかの書式は null */
export const parseHexColor = (text: string): Rgb | null => {
  const match = HEX_COLOR_PATTERN.exec(text.trim());
  const [, red, green, blue] = match ?? [];
  if (red === undefined || green === undefined || blue === undefined) {
    return null;
  }
  return {
    r: Number.parseInt(red, 16),
    g: Number.parseInt(green, 16),
    b: Number.parseInt(blue, 16),
  };
};

/** low と high を ratio (0〜1 に切り詰める) で線形に補間し、各成分を四捨五入した "rgb(r, g, b)" を返す */
export const interpolateColor = (low: Rgb, high: Rgb, ratio: number): string => {
  const f = Number.isFinite(ratio) ? Math.min(Math.max(ratio, 0), 1) : 0;
  const mix = (from: number, to: number): number => Math.round(from + (to - from) * f);
  return `rgb(${mix(low.r, high.r)}, ${mix(low.g, high.g)}, ${mix(low.b, high.b)})`;
};

/** 帯の列の値 (操作に採った数) の色の種類を返す。null (要求なし) は null、1 未満は zero、1 は one、2 以上は many */
export const adoptedCountTone = (value: number | null): AdoptedCountTone | null => {
  if (value === null) {
    return null;
  }
  if (value >= 2) {
    return "many";
  }
  return value >= 1 ? "one" : "zero";
};

/**
 * ヒートマップのセルの色を返す。advisor の問い (kind が "advisor") で値がしきい値 advisorThreshold を超える
 * (value > advisorThreshold) なら palette.hot、それ以外は値 (0〜1 に切り詰める) で low から high へ線形に補間した色
 */
export const questionCellColor = (
  value: number,
  kind: QuestionKind,
  advisorThreshold: number,
  palette: HeatPalette,
): string =>
  kind === "advisor" && value > advisorThreshold
    ? palette.hot
    : interpolateColor(palette.low, palette.high, value);
