// 選んでいる実行を読み込み中のタイムライン。読み込んだ後と同じ見出し・同じ高さのレーンに、骨組みを置く。
import { ROLE_ORDER } from "../../api/apiTypes.ts";
import { CONTROLLER_ROWS } from "../../timeline/controllerRows.ts";
import { DEFAULT_READOUT } from "../../timeline/timelineTexts.ts";
import { Panel } from "../common/Panel.tsx";
import { Skeleton } from "../common/Skeleton.tsx";
import { Lane } from "./Lane.tsx";
import styles from "./TimelineSkeleton.module.css";

/** レーンの描画の領域の高さ (Lane.module.css・AxisLane・CanvasLane・FrameChart の高さと同じ) */
const HEIGHTS = {
  axis: "1.125rem",
  bar: "1.625rem",
  strip: "0.75rem",
  input: "0.625rem",
  sharpness: "3.625rem",
  diff: "2rem",
  advisor: "1.5rem",
  event: "1.25rem",
  /** 見出しの行の文 (EmptyState の 1 行) */
  text: "1.125rem",
} as const;

export type TimelineSkeletonProps = {
  title: string;
  className?: string;
};

/** 骨組みのタイムライン (aria-busy)。レーンの並びは読み込んだ後と同じ (ヒートマップは畳んだ状態) */
export const TimelineSkeleton = ({ title, className }: TimelineSkeletonProps) => (
  <Panel
    title={title}
    meta={<Skeleton width="8rem" height="1rem" />}
    actions={
      <>
        <Skeleton width="var(--control-height)" height="var(--control-height)" />
        <Skeleton width="var(--control-height)" height="var(--control-height)" />
        <Skeleton width="8.5rem" height="var(--control-height)" />
        <Skeleton width="6rem" height="var(--control-height)" />
      </>
    }
    busy
    className={className}
  >
    <div className={styles.scroll}>
      <div className={styles.lanes}>
        <Lane label="">
          <Skeleton height={HEIGHTS.axis} className={styles.track} />
        </Lane>
        <Lane label="要求">
          <Skeleton height={HEIGHTS.bar} className={styles.track} />
        </Lane>
        <Lane label="操作に採った数">
          <Skeleton height={HEIGHTS.strip} className={styles.track} />
        </Lane>
        <Lane label="コントローラー入力">
          <Skeleton width="10rem" height={HEIGHTS.text} />
        </Lane>
        {CONTROLLER_ROWS.map((row) => (
          <Lane key={row.label} label={row.label} level="sub">
            <Skeleton height={HEIGHTS.input} className={styles.track} />
          </Lane>
        ))}
        <Lane label="意味のあるフレーム">
          <Skeleton width="20rem" height={HEIGHTS.text} />
        </Lane>
        <Lane label="" level="sub">
          <Skeleton height={HEIGHTS.sharpness} className={styles.track} />
        </Lane>
        <Lane label="" level="sub">
          <Skeleton height={HEIGHTS.diff} className={styles.track} />
        </Lane>
        <Lane label="advisor とファイル">
          <Skeleton width="14rem" height={HEIGHTS.text} />
        </Lane>
        {ROLE_ORDER.map((role) => (
          <Lane key={role} label={role} level="sub">
            <Skeleton height={HEIGHTS.advisor} className={styles.track} />
          </Lane>
        ))}
        <Lane label="イベント">
          <Skeleton height={HEIGHTS.event} className={styles.track} />
        </Lane>
      </div>
    </div>
    <p className={styles.readout} aria-hidden="true">
      {DEFAULT_READOUT}
    </p>
  </Panel>
);
