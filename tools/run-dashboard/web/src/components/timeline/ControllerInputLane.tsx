// タイムラインのコントローラー入力のレーン。pad の記録があればそれを、無ければ要求の決めた操作から組み立てた入力の
// 区間を、5 行 (十字キー / A B X Y / L R ZL ZR / + − HOME / スティック) に span で描く。
// 押下は accent、短押しは tap、スティックは accent の不透明度 0.3 + 0.7 × 倒した大きさ。
import { useMemo } from "react";
import type { PadItem, RangeResponse, RequestItem } from "../../api/apiTypes.ts";
import { inputSpansFromPad, inputSpansFromRequests } from "../../controller/controllerTimeline.ts";
import { ratioToPercent } from "../../format/formatProbability.ts";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import { type ControllerRowSpans, splitInputSpans } from "../../timeline/controllerRows.ts";
import { controllerLaneNotice } from "../../timeline/laneNotice.ts";
import { describeButtonSpan, describeStickSpan } from "../../timeline/timelineTexts.ts";
import { placeSpan, spansContaining, timeAtRatio } from "../../timeline/trackPlacement.ts";
import type { TimeRange } from "../../timeline/viewWindow.ts";
import styles from "./ControllerInputLane.module.css";
import { pointerPosition } from "./canvasPointer.ts";
import { Lane, LaneNoticeText, LaneTrack } from "./Lane.tsx";

/** スティックの区間の不透明度の下限と、倒した大きさに掛ける係数 */
const STICK_BASE_OPACITY = 0.3;
const STICK_OPACITY_SCALE = 0.7;

export type ControllerInputLaneProps = {
  pad: RangeResponse<PadItem>;
  requests: RangeResponse<RequestItem>;
  /** 中立に戻した時刻 (neutralTimesFromEvents の結果) */
  neutralTimes: readonly number[];
  /** 実行の終わり (summary.t_end ?? 0)。最後の入力はここまで効く */
  tEnd: number;
  range: TimeRange;
  /** カーソルを合わせた位置の入力の文で呼ぶ。カーソルが外れたら null */
  onHover: (text: string | null) => void;
};

/** 入力のレーン。pad と要求のどちらの記録も無い実行では「記録なし」 */
export const ControllerInputLane = ({
  pad,
  requests,
  neutralTimes,
  tEnd,
  range,
  onHover,
}: ControllerInputLaneProps) => {
  const rows = useMemo(() => {
    // 記録の配列は API の応答のまま渡す (controllerTimeline の関数が配列ごとに結果を覚える)
    if (pad.available) {
      return splitInputSpans(inputSpansFromPad(pad.items, range, tEnd));
    }
    if (requests.available) {
      return splitInputSpans(inputSpansFromRequests(requests.items, neutralTimes, range, tEnd));
    }
    return null;
  }, [pad, requests, neutralTimes, range, tEnd]);
  const notice = controllerLaneNotice(pad, requests);
  if (notice !== null || rows === null) {
    return (
      <Lane label="コントローラー入力">
        <LaneNoticeText>{notice}</LaneNoticeText>
      </Lane>
    );
  }
  return (
    <div className={styles.group}>
      <Lane label="コントローラー入力">
        <LaneNoticeText>
          {pad.available ? "pad の記録から" : "要求の操作から組み立て"}
        </LaneNoticeText>
      </Lane>
      {rows.map((rowSpans) => (
        <InputRow key={rowSpans.row.label} rowSpans={rowSpans} range={range} onHover={onHover} />
      ))}
    </div>
  );
};

type InputRowProps = {
  rowSpans: ControllerRowSpans;
  range: TimeRange;
  onHover: (text: string | null) => void;
};

/** 入力の 1 行。カーソルの位置の時刻を含む区間の説明を、値の 1 行に出す */
const InputRow = ({ rowSpans, range, onHover }: InputRowProps) => {
  const { row, buttons, sticks } = rowSpans;
  const count = buttons.length + sticks.length;
  return (
    <Lane label={row.label} level="sub">
      {/* 入力の区間は小さく数が多いため、区間ごとの説明は持たず、カーソルの位置の区間を値の 1 行に出す */}
      <div
        className={styles.trackArea}
        role="img"
        aria-label={`${row.label} の入力の区間 ${count.toLocaleString("ja-JP")} 個`}
        onMouseMove={(event) => {
          const t = timeAtRatio(pointerPosition(event).ratio, range);
          const texts = [
            ...spansContaining(buttons, t).map(describeButtonSpan),
            ...spansContaining(sticks, t).map(describeStickSpan),
          ];
          onHover(texts.length > 0 ? texts.join(" / ") : null);
        }}
        onMouseLeave={() => onHover(null)}
      >
        <LaneTrack size="input">
          {buttons.map((span) => {
            const placement = placeSpan(span.start, span.end, range);
            return placement === null ? null : (
              <span
                key={`${span.button}-${span.start}`}
                className={joinClassNames(styles.span, styles[span.kind])}
                style={{
                  left: ratioToPercent(placement.left),
                  width: ratioToPercent(placement.width),
                }}
              />
            );
          })}
          {sticks.map((span) => {
            const placement = placeSpan(span.start, span.end, range);
            return placement === null ? null : (
              <span
                key={`${span.stick}-${span.start}`}
                className={joinClassNames(styles.span, styles.stick)}
                style={{
                  left: ratioToPercent(placement.left),
                  width: ratioToPercent(placement.width),
                  opacity: STICK_BASE_OPACITY + STICK_OPACITY_SCALE * span.magnitude,
                }}
              />
            );
          })}
        </LaneTrack>
      </div>
    </Lane>
  );
};
