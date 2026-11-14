// 表示するものが無いときの文言 (「記録なし」「要求の記録なし」など)。
import type { ReactNode } from "react";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import styles from "./EmptyState.module.css";

export type EmptyStateProps = {
  /** 表示するものが無い理由の文言 */
  children: ReactNode;
  className?: string;
};

/** muted の色の短い文言 (p 要素) */
export const EmptyState = ({ children, className }: EmptyStateProps) => (
  <p className={joinClassNames(styles.empty, className)}>{children}</p>
);
