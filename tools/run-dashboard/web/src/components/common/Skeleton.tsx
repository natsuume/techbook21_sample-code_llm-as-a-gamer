// 読み込み中に、読み込んだ後の要素と同じ大きさの場所を取っておく骨組み。支援技術には読ませない (aria-hidden)。
// 骨組みを置く区画には、呼び出し側で aria-busy="true" を付ける。
import { joinClassNames } from "../../styles/joinClassNames.ts";
import styles from "./Skeleton.module.css";

export type SkeletonProps = {
  /** 幅 (CSS の長さ。例 "10rem"、"100%")。既定は "100%" */
  width?: string;
  /** 高さ (CSS の長さ。例 "1.125rem") */
  height: string;
  className?: string;
};

/** 指定した幅と高さの箱 (inline-block) */
export const Skeleton = ({ width = "100%", height, className }: SkeletonProps) => (
  <span
    aria-hidden="true"
    className={joinClassNames(styles.skeleton, className)}
    style={{ width, height }}
  />
);
