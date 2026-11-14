// タイムラインの意味のあるフレームのレーン。1 行目に表示中の区間のまとめ (谷の確定の回数・残したフレームの数・
// 確定待ちの山)、下に SVG の折れ線 2 枚 (鮮明度と谷とみなす値、直前のフレームとの差分と似ているとみなす上限) を描く。
// 折れ線を押すと、その時刻に最も近いフレームを選ぶ。折れ線は Tab で届かない (フレームは上の段の映像と詳細ペインで選べる)。
import { useMemo } from "react";
import type { FrameItem, RangeResponse } from "../../api/apiTypes.ts";
import { summarizeFrames } from "../../frames/frameMetrics.ts";
import type { TimedFrame } from "../../state/runView.ts";
import {
  chartY,
  diffScaleMax,
  nearestIndexWithin,
  polylineSegments,
  sharpnessScaleMax,
} from "../../timeline/frameChart.ts";
import { rangeLaneNotice } from "../../timeline/laneNotice.ts";
import { indexRangeWithin } from "../../timeline/rangeSearch.ts";
import { describeFrame, describeFrameSummary } from "../../timeline/timelineTexts.ts";
import { placePoint, timeAtRatio } from "../../timeline/trackPlacement.ts";
import type { TimeRange } from "../../timeline/viewWindow.ts";
import { FrameChart } from "./FrameChart.tsx";
import styles from "./FrameLane.module.css";
import { Lane, LaneNoticeText } from "./Lane.tsx";

/** 表示の区間にフレームが無いときの、まとめの行の文言 */
const NO_FRAMES_TEXT = "この区間にフレームなし";

export type FrameLaneProps = {
  frames: RangeResponse<FrameItem>;
  /** t が null でないフレーム (t の昇順) */
  timedFrames: readonly TimedFrame[];
  /** timedFrames の t の配列 */
  frameTimes: readonly number[];
  /** 似ているとみなす差分の上限 (summary.similarity_threshold) */
  similarityThreshold: number;
  range: TimeRange;
  /** 折れ線を押したときに、timedFrames の位置で呼ぶ */
  onSelect: (index: number) => void;
  /** カーソルを合わせたフレームの文で呼ぶ。カーソルが外れたら null */
  onHover: (text: string | null) => void;
};

/** フレームのレーン。frames の記録が無い実行では「記録なし」 */
export const FrameLane = ({
  frames,
  timedFrames,
  frameTimes,
  similarityThreshold,
  range,
  onSelect,
  onHover,
}: FrameLaneProps) => {
  const { start, end } = useMemo(() => indexRangeWithin(frameTimes, range), [frameTimes, range]);
  const chart = useMemo(() => {
    const inRange = timedFrames.slice(start, end);
    // 折れ線を区間の両端まで引くため、区間の前後のフレームを 1 枚ずつ足す (外側は SVG の外で切れる)
    const drawn = timedFrames.slice(Math.max(0, start - 1), Math.min(timedFrames.length, end + 1));
    const sharpnessMax = sharpnessScaleMax(inRange);
    const diffMax = diffScaleMax(inRange, similarityThreshold);
    const summary = summarizeFrames(inRange, range);
    const peak = summary.pendingPeak;
    const peakRatio = peak === null || peak.t === null ? null : placePoint(peak.t, range);
    return {
      summaryText: inRange.length > 0 ? describeFrameSummary(summary) : NO_FRAMES_TEXT,
      sharpnessMax,
      diffMax,
      sharpness: polylineSegments(drawn, (frame) => frame.sharpness, range, sharpnessMax),
      ref: polylineSegments(drawn, (frame) => frame.ref, range, sharpnessMax),
      diff: polylineSegments(drawn, (frame) => frame.diff, range, diffMax),
      thresholdY: chartY(similarityThreshold, diffMax),
      peakMark:
        peak === null || peakRatio === null
          ? null
          : { left: peakRatio, y: chartY(peak.sharpness, sharpnessMax) },
    };
  }, [timedFrames, start, end, similarityThreshold, range]);

  const notice = rangeLaneNotice(frames);
  if (notice !== null) {
    return (
      <Lane label="意味のあるフレーム">
        <LaneNoticeText>{notice}</LaneNoticeText>
      </Lane>
    );
  }

  /** カーソルの位置の時刻に最も近い、表示の区間のフレームの位置 (無ければ -1) */
  const frameIndexAt = (ratio: number): number =>
    nearestIndexWithin(frameTimes, start, end, timeAtRatio(ratio, range));
  const pointerHandlers = {
    onSelectAt: (ratio: number) => {
      const index = frameIndexAt(ratio);
      if (index >= 0) {
        onSelect(index);
      }
    },
    onHoverAt: (ratio: number) => {
      const frame = timedFrames[frameIndexAt(ratio)];
      onHover(frame === undefined ? null : describeFrame(frame));
    },
    onLeave: () => onHover(null),
  };

  return (
    <div className={styles.group}>
      <Lane label="意味のあるフレーム">
        <LaneNoticeText>{chart.summaryText}</LaneNoticeText>
      </Lane>
      <Lane
        level="sub"
        label={
          <>
            <span className={styles.legend}>
              <span className={styles.swatchSharpness} aria-hidden="true" />
              鮮明度
            </span>
            <span className={styles.legend}>
              <span className={styles.swatchRef} aria-hidden="true" />
              谷とみなす値
            </span>
          </>
        }
      >
        <FrameChart
          size="sharpness"
          label="鮮明度と谷とみなす値の折れ線。押すと、その時刻のフレームを開く"
          scaleText={chart.sharpnessMax.toLocaleString("ja-JP", { maximumFractionDigits: 0 })}
          lines={[
            { key: "ref", segments: chart.ref, className: styles.lineRef },
            { key: "sharpness", segments: chart.sharpness, className: styles.lineSharpness },
          ]}
          peakMark={chart.peakMark}
          {...pointerHandlers}
        />
      </Lane>
      <Lane
        level="sub"
        label={
          <>
            <span className={styles.legend}>
              <span className={styles.swatchDiff} aria-hidden="true" />
              直前との差分
            </span>
            <span className={styles.legend}>
              <span className={styles.swatchThreshold} aria-hidden="true" />
              上限
            </span>
          </>
        }
      >
        <FrameChart
          size="diff"
          label="直前のフレームとの差分と、似ているとみなす上限の折れ線。押すと、その時刻のフレームを開く"
          scaleText={chart.diffMax.toLocaleString("ja-JP", { maximumFractionDigits: 2 })}
          lines={[{ key: "diff", segments: chart.diff, className: styles.lineDiff }]}
          guideY={{
            y: chart.thresholdY,
            className: styles.lineThreshold,
            text: `似ているとみなす上限 ${similarityThreshold.toLocaleString("ja-JP")}`,
          }}
          peakMark={null}
          {...pointerHandlers}
        />
      </Lane>
    </div>
  );
};
