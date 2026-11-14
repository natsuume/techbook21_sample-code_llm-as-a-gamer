// タイムラインの操作に採った数のレーン。0.5 秒ごとの列の最大値を canvas の帯に描く (0 は surface-2、1 は hot-soft、
// 2 以上は hot)。押すとその列の値を決めた要求を選ぶ。帯は Tab で届かない (要求は前後の要求のボタンで選べる)。
import { useCallback, useMemo } from "react";
import type { RangeResponse, RequestItem } from "../../api/apiTypes.ts";
import { binColumnRects, binIndexAt } from "../../timeline/binColumns.ts";
import { adoptedCountTone } from "../../timeline/cellColors.ts";
import { type BinCell, binAdoptedCounts } from "../../timeline/halfSecondBins.ts";
import { requestValueLaneNotice } from "../../timeline/laneNotice.ts";
import { type RequestTimeIndex, requestsInBins } from "../../timeline/requestWindow.ts";
import { describeAdoptedCell } from "../../timeline/timelineTexts.ts";
import type { TimeRange } from "../../timeline/viewWindow.ts";
import styles from "./CanvasLane.module.css";
import { pointerPosition } from "./canvasPointer.ts";
import { Lane, LaneNoticeText } from "./Lane.tsx";
import { type CanvasPainter, useCanvasPaint } from "./useCanvasPaint.ts";

export type AdoptedCountLaneProps = {
  /** 古い形の実行 (summary.old_format) */
  oldFormat: boolean;
  requests: RangeResponse<RequestItem>;
  /** requests.items から buildRequestTimeIndex で作った並び */
  timeIndex: RequestTimeIndex;
  range: TimeRange;
  /** 列を押したときに、その列の値を決めた要求の番号で呼ぶ */
  onSelect: (n: number) => void;
  /** カーソルを合わせた列の文で呼ぶ。カーソルが外れたら null */
  onHover: (text: string | null) => void;
};

/** 帯のレーン。古い形の実行と、要求の記録が無い実行では文言だけを出す */
export const AdoptedCountLane = ({
  oldFormat,
  requests,
  timeIndex,
  range,
  onSelect,
  onHover,
}: AdoptedCountLaneProps) => {
  const notice = requestValueLaneNotice(oldFormat, requests);
  const cells = useMemo(
    () =>
      notice === null
        ? binAdoptedCounts(requestsInBins(requests.items, timeIndex, range), range)
        : [],
    [notice, requests.items, timeIndex, range],
  );
  return (
    <Lane label="操作に採った数">
      {notice === null ? (
        <AdoptedCountStrip cells={cells} range={range} onSelect={onSelect} onHover={onHover} />
      ) : (
        <LaneNoticeText>{notice}</LaneNoticeText>
      )}
    </Lane>
  );
};

type AdoptedCountStripProps = {
  cells: BinCell[];
  range: TimeRange;
  onSelect: (n: number) => void;
  onHover: (text: string | null) => void;
};

/** 帯の canvas と、それを包む押せる領域 */
const AdoptedCountStrip = ({ cells, range, onSelect, onHover }: AdoptedCountStripProps) => {
  const paint = useCallback<CanvasPainter>(
    ({ context, width, height, readColor }) => {
      const colors = {
        zero: readColor("--surface-2"),
        one: readColor("--hot-soft"),
        many: readColor("--hot"),
      };
      const rects = binColumnRects(cells, range, width);
      cells.forEach((cell, index) => {
        const tone = adoptedCountTone(cell.value);
        const rect = rects[index];
        if (tone === null || rect === undefined || rect.width === 0) {
          return;
        }
        context.fillStyle = colors[tone];
        context.fillRect(rect.x, 0, rect.width, height);
      });
    },
    [cells, range],
  );
  const canvasRef = useCanvasPaint(paint);
  const cellAt = (ratio: number): BinCell | undefined => cells[binIndexAt(cells, range, ratio)];
  return (
    <button
      type="button"
      tabIndex={-1}
      className={styles.area}
      aria-label="操作に採った数の 0.5 秒ごとの帯。押すと、その列の値を決めた要求を開く"
      onClick={(event) => {
        const owner = cellAt(pointerPosition(event).ratio)?.owner;
        if (owner !== null && owner !== undefined) {
          onSelect(owner);
        }
      }}
      onMouseMove={(event) => {
        const cell = cellAt(pointerPosition(event).ratio);
        onHover(cell === undefined ? null : describeAdoptedCell(cell));
      }}
      onMouseLeave={() => onHover(null)}
    >
      <canvas ref={canvasRef} className={styles.strip} />
    </button>
  );
};
