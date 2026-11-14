// タイムラインのレーン 1 行の枠。左に見出し (幅 --label-w)、右に描画の領域 (トラック) を並べる。
import type { ReactNode } from "react";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import { EmptyState } from "../common/EmptyState.tsx";
import styles from "./Lane.module.css";

/** 見出しの段。lane はレーンの見出し、sub はレーンの中の行の見出し (字下げと小さい文字) */
export type LaneLabelLevel = "lane" | "sub";
/** 見出しとトラックの縦の揃え方。center は中央、top は上端 */
export type LaneAlign = "center" | "top";

export type LaneProps = {
  /** 見出し */
  label: ReactNode;
  /** 既定は lane */
  level?: LaneLabelLevel;
  /** 既定は center */
  align?: LaneAlign;
  /** トラック (LaneTrack・LaneNoticeText など) */
  children: ReactNode;
};

/** 見出しとトラックを横に並べた 1 行 */
export const Lane = ({ label, level = "lane", align = "center", children }: LaneProps) => (
  <div className={joinClassNames(styles.lane, align === "top" && styles.top)}>
    <div className={joinClassNames(styles.label, level === "sub" && styles.sub)}>{label}</div>
    {children}
  </div>
);

/** トラックの高さの種類。モックのトラックの高さ (26・10・24・20・16px) に合わせる */
export type LaneTrackSize = "bar" | "input" | "advisor" | "event";

export type LaneTrackProps = {
  /** 既定は bar */
  size?: LaneTrackSize;
  className?: string;
  /** トラックの上に絶対位置で置く要素 */
  children?: ReactNode;
};

/** 地 surface-2 の描画の領域 (position: relative。はみ出した要素は切る) */
export const LaneTrack = ({ size = "bar", className, children }: LaneTrackProps) => (
  <div className={joinClassNames(styles.track, styles[size], className)}>{children}</div>
);

/** レーンの描くものが無いときの文言 (「記録なし」など)。トラックの代わりに置く */
export const LaneNoticeText = ({ children }: { children: ReactNode }) => (
  <EmptyState className={styles.notice}>{children}</EmptyState>
);
