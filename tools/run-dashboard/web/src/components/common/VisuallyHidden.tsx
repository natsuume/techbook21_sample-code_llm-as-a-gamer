// 画面には出さず、支援技術 (スクリーンリーダー) にだけ読ませる文字。押せない理由や補足の説明に使う。
import type { ReactNode } from "react";
import styles from "./VisuallyHidden.module.css";

export type VisuallyHiddenProps = {
  /** aria-describedby などから参照するときの id */
  id?: string;
  /** 読ませる文字 */
  children: ReactNode;
};

/** 見た目の大きさを持たない span (位置は absolute のため、flex や grid の並びにも入らない) */
export const VisuallyHidden = ({ id, children }: VisuallyHiddenProps) => (
  <span id={id} className={styles.hidden}>
    {children}
  </span>
);
