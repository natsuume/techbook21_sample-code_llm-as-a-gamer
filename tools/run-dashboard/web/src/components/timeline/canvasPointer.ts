// canvas などの描画の領域で、カーソルの位置を領域の中の位置 (横は幅に対する比率、縦は上からの px) にする関数。
import type { MouseEvent } from "react";

/** 領域の中のカーソルの位置。ratio は左端 0・右端 1 の比率、y は上端からの CSS の px */
export type PointerPosition = { ratio: number; y: number };

/** マウスの出来事の位置を、出来事を受けた要素 (currentTarget) の中の位置にする。幅が 0 なら ratio は 0 */
export const pointerPosition = (event: MouseEvent<Element>): PointerPosition => {
  const rect = event.currentTarget.getBoundingClientRect();
  return {
    ratio: rect.width > 0 ? (event.clientX - rect.left) / rect.width : 0,
    y: event.clientY - rect.top,
  };
};
