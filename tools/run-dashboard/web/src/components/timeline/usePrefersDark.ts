// OS の設定が暗いテーマ (prefers-color-scheme: dark) かを、変わったら描き直す形で返すフック。
import { useSyncExternalStore } from "react";

const DARK_QUERY = "(prefers-color-scheme: dark)";

const subscribe = (onChange: () => void): (() => void) => {
  const media = window.matchMedia(DARK_QUERY);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
};

const getSnapshot = (): boolean => window.matchMedia(DARK_QUERY).matches;

/** 暗いテーマなら true。OS の設定が変わると、使っているコンポーネントを描き直す */
export const usePrefersDark = (): boolean => useSyncExternalStore(subscribe, getSnapshot);
