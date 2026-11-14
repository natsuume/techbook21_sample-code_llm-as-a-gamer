// タイムラインの要求のレーン。表示の区間と重なる要求を、送信から受信までの棒 (button) で描く。押すとその要求を選ぶ。
// 棒の数が多いため、棒は Tab で届かない (tabIndex -1)。キーボードでは見出しの行の前の要求・次の要求のボタンで選ぶ。
// ライブでは、最後の要求の後の応答待ちの区間を、押せない破線の枠で描く。
import { useMemo } from "react";
import type { RangeResponse, RequestItem } from "../../api/apiTypes.ts";
import { ratioToPercent } from "../../format/formatProbability.ts";
import type { PendingSpan } from "../../live/pendingRequest.ts";
import { categorizeRequest } from "../../requests/requestCategory.ts";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import { rangeLaneNotice } from "../../timeline/laneNotice.ts";
import {
  barEnd,
  type RequestTimeIndex,
  requestsOverlapping,
} from "../../timeline/requestWindow.ts";
import { cachedDescriber } from "../../timeline/textCache.ts";
import { describePendingSpan, describeRequestBar } from "../../timeline/timelineTexts.ts";
import { placeSpan } from "../../timeline/trackPlacement.ts";
import type { TimeRange } from "../../timeline/viewWindow.ts";
import { Lane, LaneNoticeText, LaneTrack } from "./Lane.tsx";
import styles from "./RequestLane.module.css";

/** 要求の棒の説明 (要求ごとに 1 回だけ作る) */
const describeBar = cachedDescriber(describeRequestBar);

export type RequestLaneProps = {
  requests: RangeResponse<RequestItem>;
  /** requests.items から buildRequestTimeIndex で作った並び */
  timeIndex: RequestTimeIndex;
  range: TimeRange;
  /** 選んでいる要求の番号 */
  selectedN: number | null;
  /** 棒を押したときに、その要求の番号で呼ぶ */
  onSelect: (n: number) => void;
  /** 応答待ちの区間 (ライブのときだけ)。無ければ null */
  pending: PendingSpan | null;
  /** 応答待ちの枠にカーソルを合わせたときにその説明で、外したときに null で呼ぶ */
  onHover: (text: string | null) => void;
};

/**
 * 要求の棒のレーン。棒の色と高さは categorizeRequest の種類で変える (選んでいる要求は accent の地と枠で高く、
 * 失敗は破線の枠、advisor を呼んだ要求は hot、操作を送った要求は tap、ほかは bar)。
 * 応答待ちの区間は、通常の要求の棒と同じ高さの muted の破線の枠 (地なし) で描く。押せない (button にせず、Tab でも
 * 届かない) 要素で、説明は aria-label・title と、カーソルを合わせた値の 1 行に出す
 */
export const RequestLane = ({
  requests,
  timeIndex,
  range,
  selectedN,
  onSelect,
  pending,
  onHover,
}: RequestLaneProps) => {
  const visible = useMemo(
    () => requestsOverlapping(requests.items, timeIndex, range),
    [requests.items, timeIndex, range],
  );
  const notice = rangeLaneNotice(requests);
  const pendingPlacement = pending === null ? null : placeSpan(pending.start, pending.end, range);
  const pendingText = pending === null ? "" : describePendingSpan(pending);
  return (
    <Lane label="要求">
      {notice === null ? (
        <LaneTrack>
          {pendingPlacement === null ? null : (
            <span
              role="img"
              className={styles.pending}
              style={{
                left: ratioToPercent(pendingPlacement.left),
                width: ratioToPercent(pendingPlacement.width),
              }}
              aria-label={pendingText}
              title={pendingText}
              onMouseEnter={() => onHover(pendingText)}
              onMouseLeave={() => onHover(null)}
            />
          )}
          {visible.map((request) => {
            const placement = placeSpan(request.t_sent, barEnd(request), range);
            if (placement === null) {
              return null;
            }
            const category = categorizeRequest(request, selectedN);
            const description = describeBar(request);
            return (
              <button
                key={`${request.n}-${request.t_sent}`}
                type="button"
                tabIndex={-1}
                className={joinClassNames(styles.bar, styles[category])}
                style={{
                  left: ratioToPercent(placement.left),
                  width: ratioToPercent(placement.width),
                }}
                aria-label={description}
                aria-current={category === "selected" ? "true" : undefined}
                title={description}
                onClick={() => onSelect(request.n)}
              />
            );
          })}
        </LaneTrack>
      ) : (
        <LaneNoticeText>{notice}</LaneNoticeText>
      )}
    </Lane>
  );
};
