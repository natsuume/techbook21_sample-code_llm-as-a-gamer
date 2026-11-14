// 画面の devicePixelRatio (CSS の 1px あたりの物理的な画素の数) を、変わったら描き直す形で返すフック。
// ブラウザーの拡大や、画素の密度の違う画面への移動で変わる。
import { useSyncExternalStore } from "react";

/**
 * devicePixelRatio の変化を知らせる。今の値の resolution の media query が外れたら (値が変わったら) onChange を呼び、
 * 新しい値の media query を見張り直す
 */
const subscribe = (onChange: () => void): (() => void) => {
  let media: MediaQueryList | null = null;
  const handleChange = (): void => {
    watch();
    onChange();
  };
  const watch = (): void => {
    media?.removeEventListener("change", handleChange);
    media = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
    media.addEventListener("change", handleChange);
  };
  watch();
  return () => media?.removeEventListener("change", handleChange);
};

const getSnapshot = (): number => window.devicePixelRatio;

/** 今の devicePixelRatio を返す。変わると、使っているコンポーネントを描き直す */
export const useDevicePixelRatio = (): number => useSyncExternalStore(subscribe, getSnapshot);
