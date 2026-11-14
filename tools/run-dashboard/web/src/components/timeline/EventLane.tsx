// タイムラインのイベントのレーン。run.jsonl の Decision Model の失敗とキャプチャーの停止を点、ブリッジの復旧待ちを
// 区間で描く (閉じる行の無い復旧待ちは破線)。表示の区間に無ければ「この区間に失敗・復旧待ちなし」。
import type { EventItem, RangeResponse } from "../../api/apiTypes.ts";
import type { EventMark } from "../../events/eventMarks.ts";
import { ratioToPercent } from "../../format/formatProbability.ts";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import { rangeLaneNotice } from "../../timeline/laneNotice.ts";
import { cachedDescriber } from "../../timeline/textCache.ts";
import { describeEventMark } from "../../timeline/timelineTexts.ts";
import { placePoint, placeSpan } from "../../timeline/trackPlacement.ts";
import type { TimeRange } from "../../timeline/viewWindow.ts";
import styles from "./EventLane.module.css";
import { Lane, LaneNoticeText, LaneTrack } from "./Lane.tsx";

/** イベントの印の説明 (印ごとに 1 回だけ作る) */
const describeMark = cachedDescriber(describeEventMark);

/** 表示の区間に印が無いときの文言 */
const NO_EVENTS_TEXT = "この区間に失敗・復旧待ちなし";

export type EventLaneProps = {
  events: RangeResponse<EventItem>;
  /** buildEventMarks(events.items, tEnd) の結果 */
  marks: readonly EventMark[];
  range: TimeRange;
};

/** 表示の区間の印の位置 */
type PlacedMark = { mark: EventMark; index: number; left: number; width: number | null };

/**
 * 印を表示の区間に置く。点は width が null、区間は幅の比率。区間の外の印は入れない。index は marks の中の位置
 * (実行ごとに変わらないため、要素の key に使う)
 */
const placeMarks = (marks: readonly EventMark[], range: TimeRange): PlacedMark[] =>
  marks.flatMap((mark, index): PlacedMark[] => {
    if (mark.kind === "bridge_wait") {
      const placement = placeSpan(mark.start, mark.end, range);
      return placement === null ? [] : [{ mark, index, ...placement }];
    }
    const left = placePoint(mark.t, range);
    return left === null ? [] : [{ mark, index, left, width: null }];
  });

/** イベントのレーン。run.jsonl の記録が無い実行では「記録なし」 */
export const EventLane = ({ events, marks, range }: EventLaneProps) => {
  const notice = rangeLaneNotice(events);
  if (notice !== null) {
    return (
      <Lane label="イベント">
        <LaneNoticeText>{notice}</LaneNoticeText>
      </Lane>
    );
  }
  const placed = placeMarks(marks, range);
  return (
    <Lane label="イベント">
      <LaneTrack size="event">
        {placed.length === 0 ? <span className={styles.empty}>{NO_EVENTS_TEXT}</span> : null}
        {placed.map(({ mark, index, left, width }) => {
          const description = describeMark(mark);
          return (
            <span
              key={index}
              role="img"
              aria-label={description}
              title={description}
              className={joinClassNames(
                styles.mark,
                styles[mark.kind],
                mark.kind === "bridge_wait" && mark.open && styles.open,
              )}
              style={
                width === null
                  ? { left: ratioToPercent(left) }
                  : { left: ratioToPercent(left), width: ratioToPercent(width) }
              }
            />
          );
        })}
      </LaneTrack>
    </Lane>
  );
};
