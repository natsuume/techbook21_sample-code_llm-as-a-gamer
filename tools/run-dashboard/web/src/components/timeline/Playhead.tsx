// タイムラインの全レーンに重ねる、再生位置の縦線 (2px の accent) とラベル "t=…"。右端に近ければラベルを線の左に置く。
import { playheadLabel } from "../../timeline/timelineTexts.ts";
import { playheadLabelSide } from "../../timeline/trackPlacement.ts";
import { ratioInRange, type TimeRange } from "../../timeline/viewWindow.ts";
import styles from "./Playhead.module.css";

export type PlayheadProps = {
  /** 再生位置 (経過秒) */
  position: number;
  range: TimeRange;
};

/**
 * レーンを包む要素 (position: relative) の上に絶対位置で置く。左の位置は見出しの幅 --label-w に、トラックの幅 ×
 * ratioInRange(position, range) を足したもの。支援技術には読ませない (再生位置は上部のバーで読む)
 */
export const Playhead = ({ position, range }: PlayheadProps) => {
  const ratio = ratioInRange(position, range);
  return (
    <div
      className={styles.playhead}
      style={{ left: `calc(var(--label-w) + (100% - var(--label-w)) * ${ratio})` }}
      aria-hidden="true"
    >
      <span className={styles[playheadLabelSide(ratio)]}>{playheadLabel(position)}</span>
    </div>
  );
};
