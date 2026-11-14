// 意味のあるフレームのレーンの折れ線 1 枚。SVG (viewBox の幅 1000・高さ 100、縦横を伸ばして描き、線の太さは
// 伸ばさない) に折れ線と横線を描き、左上に縦の上限の値、確定待ちの山の印を重ねる。押せる領域 (button) で包む。
import { ratioToPercent } from "../../format/formatProbability.ts";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import { CHART_VIEW_HEIGHT, CHART_VIEW_WIDTH } from "../../timeline/frameChart.ts";
import { pointerPosition } from "./canvasPointer.ts";
import styles from "./FrameChart.module.css";

/** 折れ線の高さの種類。sharpness は鮮明度 (3.625rem)、diff は差分 (2rem) */
export type FrameChartSize = "sharpness" | "diff";

/** 折れ線 1 本。segments は polylineSegments の結果 (切れた区間ごとの points の値) */
export type FrameChartLine = { key: string; segments: readonly string[]; className?: string };

export type FrameChartProps = {
  size: FrameChartSize;
  /** 押せる領域の名前 (aria-label) */
  label: string;
  /** 縦の上限の値の文字 (左上に出す) */
  scaleText: string;
  /** 描く折れ線。後の線ほど上に描く */
  lines: readonly FrameChartLine[];
  /** 横線 (しきい値など)。y は viewBox の値、text は右下に出す線の名前と値 */
  guideY?: { y: number; className?: string; text?: string };
  /** 確定待ちの山の印。left はトラックの幅に対する比率、y は viewBox の値。無ければ null */
  peakMark: { left: number; y: number } | null;
  /** 押したときに、カーソルの位置の比率 (0〜1) で呼ぶ */
  onSelectAt: (ratio: number) => void;
  /** カーソルを動かしたときに、カーソルの位置の比率 (0〜1) で呼ぶ */
  onHoverAt: (ratio: number) => void;
  /** カーソルが外れたときに呼ぶ */
  onLeave: () => void;
};

/** points の値の最初の点 ("x,y")。切れた区間は時刻の順に並ぶため、区間を区別する key に使う */
const firstPoint = (points: string): string => points.split(" ", 1)[0] ?? points;

/** 折れ線の SVG と、それを包む押せる領域 */
export const FrameChart = ({
  size,
  label,
  scaleText,
  lines,
  guideY,
  peakMark,
  onSelectAt,
  onHoverAt,
  onLeave,
}: FrameChartProps) => (
  <button
    type="button"
    tabIndex={-1}
    className={joinClassNames(styles.chart, styles[size])}
    aria-label={label}
    onClick={(event) => onSelectAt(pointerPosition(event).ratio)}
    onMouseMove={(event) => onHoverAt(pointerPosition(event).ratio)}
    onMouseLeave={onLeave}
  >
    <svg
      className={styles.svg}
      viewBox={`0 0 ${CHART_VIEW_WIDTH} ${CHART_VIEW_HEIGHT}`}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      {guideY === undefined ? null : (
        <line
          className={guideY.className}
          x1={0}
          x2={CHART_VIEW_WIDTH}
          y1={guideY.y}
          y2={guideY.y}
          vectorEffect="non-scaling-stroke"
        />
      )}
      {lines.flatMap((line) =>
        line.segments.map((points) => (
          <polyline
            key={`${line.key}-${firstPoint(points)}`}
            className={line.className}
            points={points}
            vectorEffect="non-scaling-stroke"
          />
        )),
      )}
    </svg>
    <span className={styles.scale} aria-hidden="true">
      {scaleText}
    </span>
    {guideY?.text === undefined ? null : (
      <span className={styles.tag} aria-hidden="true">
        {guideY.text}
      </span>
    )}
    {peakMark === null ? null : (
      <span
        className={styles.peak}
        style={{
          left: ratioToPercent(peakMark.left),
          top: ratioToPercent(peakMark.y / CHART_VIEW_HEIGHT),
        }}
        aria-hidden="true"
      />
    )}
  </button>
);
