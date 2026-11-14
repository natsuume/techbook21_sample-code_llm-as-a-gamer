// タイムラインの目盛りのレーン。表示の区間の目盛りの時刻に、縦の短い線と "t=…" のラベルを置く。
import { ratioToPercent } from "../../format/formatProbability.ts";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import { axisTickMarks } from "../../timeline/axisTickMarks.ts";
import type { TimeRange } from "../../timeline/viewWindow.ts";
import styles from "./AxisLane.module.css";
import { Lane } from "./Lane.tsx";

export type AxisLaneProps = {
  range: TimeRange;
};

/** 目盛りの線とラベル。支援技術には読ませない (表示中の区間は見出しの行で読む) */
export const AxisLane = ({ range }: AxisLaneProps) => (
  <Lane label="">
    <div className={styles.axis} aria-hidden="true">
      {axisTickMarks(range).map((mark) => (
        <span
          key={mark.t}
          className={joinClassNames(styles.tick, styles[mark.align])}
          style={{ left: ratioToPercent(mark.ratio) }}
        >
          {mark.label}
        </span>
      ))}
    </div>
  </Lane>
);
