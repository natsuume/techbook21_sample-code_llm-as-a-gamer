// canvas を、CSS の大きさ × devicePixelRatio の画素で描くフック。描く関数・canvas の大きさ・テーマ・
// devicePixelRatio が変わったら描き直す。描く関数は CSS の px の座標で描く。
import { type RefObject, useLayoutEffect, useRef } from "react";
import { useDevicePixelRatio } from "./useDevicePixelRatio.ts";
import { useElementSize } from "./useElementSize.ts";
import { usePrefersDark } from "./usePrefersDark.ts";

/** 描く関数に渡す値。width・height は canvas の CSS の大きさ (px)、readColor は CSS 変数の値を読む */
export type CanvasPaintFrame = {
  context: CanvasRenderingContext2D;
  width: number;
  height: number;
  /** 色を読んだときのテーマ */
  colorScheme: "light" | "dark";
  /** CSS 変数 (例 "--hot") の値を、前後の空白を除いて返す */
  readColor: (name: string) => string;
};

/** canvas に描く関数。描き直すたびに、消した canvas に描く */
export type CanvasPainter = (frame: CanvasPaintFrame) => void;

/**
 * canvas の画素の大きさを CSS の大きさ × pixelRatio にし、CSS の px で描けるように座標を拡大してから paint を呼ぶ。
 * 大きさが 0 のときは描かない
 */
const paintCanvas = (
  canvas: HTMLCanvasElement,
  width: number,
  height: number,
  pixelRatio: number,
  colorScheme: "light" | "dark",
  paint: CanvasPainter,
): void => {
  if (!(width > 0 && height > 0)) {
    return;
  }
  canvas.width = Math.round(width * pixelRatio);
  canvas.height = Math.round(height * pixelRatio);
  const context = canvas.getContext("2d");
  if (context === null) {
    return;
  }
  context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  context.clearRect(0, 0, width, height);
  const style = getComputedStyle(canvas);
  paint({
    context,
    width,
    height,
    colorScheme,
    readColor: (name) => style.getPropertyValue(name).trim(),
  });
};

/**
 * 返した ref を canvas に付けると、paint で描く。paint は描く値が変わったときだけ作り直す (useCallback) と、
 * 描き直しを減らせる。描くのは DOM を変えた後・画面に出す前 (useLayoutEffect) で、最初の描画では ResizeObserver の
 * 通知の前に clientWidth・clientHeight の大きさで描く
 */
export const useCanvasPaint = (paint: CanvasPainter): RefObject<HTMLCanvasElement | null> => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const size = useElementSize(canvasRef);
  const dark = usePrefersDark();
  const pixelRatio = useDevicePixelRatio();
  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) {
      return;
    }
    const width = size.width > 0 ? size.width : canvas.clientWidth;
    const height = size.height > 0 ? size.height : canvas.clientHeight;
    paintCanvas(canvas, width, height, pixelRatio, dark ? "dark" : "light", paint);
  }, [paint, size, pixelRatio, dark]);
  return canvasRef;
};
