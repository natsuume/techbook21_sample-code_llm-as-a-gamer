// 要素の中身の大きさ (content box の CSS の px) を ResizeObserver で追うフック。
import { type RefObject, useEffect, useState } from "react";

/** 要素の中身の幅と高さ (CSS の px) */
export type ElementSize = { width: number; height: number };

const EMPTY_SIZE: ElementSize = { width: 0, height: 0 };

/**
 * ref の要素の中身の大きさを返す。大きさが変わったら描き直す (同じ大きさなら同じオブジェクトのまま)。
 * 最初の通知が届く前は { width: 0, height: 0 }。ref の要素は、このフックを呼ぶコンポーネントと一緒に置く
 * (後から別の要素に差し替えない)
 */
export const useElementSize = (ref: RefObject<Element | null>): ElementSize => {
  const [size, setSize] = useState<ElementSize>(EMPTY_SIZE);
  useEffect(() => {
    const element = ref.current;
    if (element === null) {
      return;
    }
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry === undefined) {
        return;
      }
      const box = entry.contentBoxSize[0];
      const width = box?.inlineSize ?? entry.contentRect.width;
      const height = box?.blockSize ?? entry.contentRect.height;
      setSize((current) =>
        current.width === width && current.height === height ? current : { width, height },
      );
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return size;
};
