// タイムラインの問いの値のヒートマップ (操作に採った数のレーンを展開したとき)。問いごと・0.5 秒ごとの列の最大値を、
// 値 0〜1 で heat-low から heat-high への色で canvas に塗る (advisor の問いでしきい値を超えた値は hot)。
// セルを押すと、その値を決めた要求を選び、その問いを強調する。ヒートマップは Tab で届かない。
import { useCallback, useMemo } from "react";
import type { RangeResponse, RequestItem } from "../../api/apiTypes.ts";
import { binColumnRects, binIndexAt } from "../../timeline/binColumns.ts";
import { type HeatPalette, parseHexColor, questionCellColor } from "../../timeline/cellColors.ts";
import { type BinCell, binQuestionValues } from "../../timeline/halfSecondBins.ts";
import { type HeatmapLayout, heatmapRowAt } from "../../timeline/heatmapLayout.ts";
import type { LaneNotice } from "../../timeline/laneNotice.ts";
import { type RequestTimeIndex, requestsInBins } from "../../timeline/requestWindow.ts";
import { describeQuestionCell } from "../../timeline/timelineTexts.ts";
import type { TimeRange } from "../../timeline/viewWindow.ts";
import styles from "./CanvasLane.module.css";
import { pointerPosition } from "./canvasPointer.ts";
import { Lane, LaneNoticeText } from "./Lane.tsx";
import { type CanvasPainter, useCanvasPaint } from "./useCanvasPaint.ts";

export type QuestionHeatmapLaneProps = {
  /** レーンの要素の id (展開のボタンの aria-controls から参照する) */
  id: string;
  /** レーンの文言 (null ならヒートマップを描く。文言があれば、それだけを出す) */
  notice: LaneNotice;
  requests: RangeResponse<RequestItem>;
  /** requests.items から buildRequestTimeIndex で作った並び */
  timeIndex: RequestTimeIndex;
  /** summary.questions から heatmapLayout で作った行の並び */
  layout: HeatmapLayout;
  /** summary.questions の数 (要求の p の長さ) */
  questionCount: number;
  /** advisor の問いのしきい値 (summary.thresholds.advisor) */
  advisorThreshold: number;
  range: TimeRange;
  /** セルを押したときに、その値を決めた要求の番号と問いの名前で呼ぶ */
  onSelect: (n: number, question: string) => void;
  /** カーソルを合わせたセルの文で呼ぶ。カーソルが外れたら null */
  onHover: (text: string | null) => void;
};

/** ヒートマップのレーン。notice があれば (古い形の実行・要求の記録が無い実行・問いが無い実行) 文言だけを出す */
export const QuestionHeatmapLane = ({
  id,
  notice,
  requests,
  timeIndex,
  layout,
  questionCount,
  advisorThreshold,
  range,
  onSelect,
  onHover,
}: QuestionHeatmapLaneProps) => {
  const values = useMemo(
    () =>
      notice === null
        ? binQuestionValues(requestsInBins(requests.items, timeIndex, range), questionCount, range)
        : [],
    [notice, requests.items, timeIndex, questionCount, range],
  );
  return (
    <div id={id}>
      {notice === null ? (
        <Lane label={<HeatmapGroupLabels layout={layout} />} level="sub" align="top">
          <HeatmapCanvas
            values={values}
            layout={layout}
            advisorThreshold={advisorThreshold}
            range={range}
            onSelect={onSelect}
            onHover={onHover}
          />
        </Lane>
      ) : (
        <Lane label="問いの値" level="sub">
          <LaneNoticeText>{notice}</LaneNoticeText>
        </Lane>
      )}
    </div>
  );
};

/** 見出しの列に、kind のまとまりの名前 (例 "button 17") を、まとまりの行の上端の高さに置く */
const HeatmapGroupLabels = ({ layout }: { layout: HeatmapLayout }) => (
  <div className={styles.groups} style={{ height: `${layout.height}px` }}>
    {layout.groups.map((group) => (
      <span key={group.kind} className={styles.groupLabel} style={{ top: `${group.y}px` }}>
        {group.label}
      </span>
    ))}
  </div>
);

type HeatmapCanvasProps = {
  /** binQuestionValues の結果 ([問いの位置][列]) */
  values: BinCell[][];
  layout: HeatmapLayout;
  advisorThreshold: number;
  range: TimeRange;
  onSelect: (n: number, question: string) => void;
  onHover: (text: string | null) => void;
};

/** ヒートマップの canvas と、それを包む押せる領域 */
const HeatmapCanvas = ({
  values,
  layout,
  advisorThreshold,
  range,
  onSelect,
  onHover,
}: HeatmapCanvasProps) => {
  const columns = values[0] ?? [];
  const paint = useCallback<CanvasPainter>(
    ({ context, width, readColor }) => {
      const low = parseHexColor(readColor("--heat-low"));
      const high = parseHexColor(readColor("--heat-high"));
      if (low === null || high === null) {
        return;
      }
      const palette: HeatPalette = { low, high, hot: readColor("--hot") };
      const rects = binColumnRects(columns, range, width);
      for (const row of layout.rows) {
        const cells = values[row.questionIndex] ?? [];
        cells.forEach((cell, index) => {
          const rect = rects[index];
          if (cell.value === null || rect === undefined || rect.width === 0) {
            return;
          }
          context.fillStyle = questionCellColor(cell.value, row.kind, advisorThreshold, palette);
          context.fillRect(rect.x, row.y, rect.width, row.height);
        });
      }
    },
    [columns, values, layout, advisorThreshold, range],
  );
  const canvasRef = useCanvasPaint(paint);
  /** カーソルの位置のセルと、その行の問いの名前 (行の外か列が無ければ null) */
  const cellAt = (ratio: number, y: number): { cell: BinCell; question: string } | null => {
    const row = heatmapRowAt(layout, y);
    const cell = values[row?.questionIndex ?? -1]?.[binIndexAt(columns, range, ratio)];
    return row === null || cell === undefined ? null : { cell, question: row.name };
  };
  return (
    <button
      type="button"
      tabIndex={-1}
      className={styles.area}
      aria-label={`${layout.rows.length} の問いの値の 0.5 秒ごとのヒートマップ。押すと、そのセルの値を決めた要求を開き、その問いを強調する`}
      onClick={(event) => {
        const position = pointerPosition(event);
        const hit = cellAt(position.ratio, position.y);
        if (hit !== null && hit.cell.owner !== null) {
          onSelect(hit.cell.owner, hit.question);
        }
      }}
      onMouseMove={(event) => {
        const position = pointerPosition(event);
        const hit = cellAt(position.ratio, position.y);
        onHover(hit === null ? null : describeQuestionCell(hit.question, hit.cell));
      }}
      onMouseLeave={() => onHover(null)}
    >
      <canvas ref={canvasRef} style={{ height: `${layout.height}px` }} />
    </button>
  );
};
