// 詳細ペインのフレームのタブ。開いているフレームの縮小画像、前後 3 枚の小さな画像、そのフレームを載せた要求、
// 判定の値 (鮮明度・判定の状態・今の区間の山・谷とみなす値・直前との差分・谷までの残り)、表示中の区間のまとめを出す。
import { useMemo, useRef } from "react";
import { filePath } from "../../api/apiPaths.ts";
import { frameTabNotice, NO_REQUEST_RECORD_NOTICE } from "../../detail/detailNotices.ts";
import {
  FRAME_REQUEST_CANDIDATES,
  formatDecimal,
  framePhaseText,
  NEIGHBOR_RADIUS,
  neighborFrameSlots,
  requestsShowingFrame,
  resolveFrameIndex,
  valleyDistanceLabel,
} from "../../detail/frameDetail.ts";
import { formatSeconds } from "../../format/formatValues.ts";
import { requestsAfter, summarizeFrames, valleyDistance } from "../../frames/frameMetrics.ts";
import type { TimedFrame } from "../../state/runView.ts";
import type { RunView } from "../../state/useRunView.ts";
import { useViewState } from "../../state/ViewStateContext.tsx";
import { NO_RECORD_NOTICE } from "../../timeline/laneNotice.ts";
import { indexRangeWithin } from "../../timeline/rangeSearch.ts";
import { describeFrameSummary, describeRange } from "../../timeline/timelineTexts.ts";
import { Button } from "../common/Button.tsx";
import { EmptyState } from "../common/EmptyState.tsx";
import { ErrorState } from "../common/ErrorState.tsx";
import { IconButton } from "../common/IconButton.tsx";
import { Skeleton } from "../common/Skeleton.tsx";
import content from "./DetailContent.module.css";
import { DetailImage } from "./DetailImage.tsx";
import styles from "./FrameTab.module.css";
import { useRequestDetails } from "./useRequestDetails.ts";
import { useScrollIntoView } from "./useScrollIntoView.ts";

/** フレームのタブ。フレームの記録が無い実行では、理由の文言だけを出す */
export const FrameTab = ({ view }: { view: RunView }) => {
  const { state } = useViewState();
  const notice = frameTabNotice(view.frames, view.timedFrames.length);
  const index = resolveFrameIndex(state.frameIndex, view.frameTimes, view.position);
  const frame = index === null ? undefined : view.timedFrames[index];
  if (notice !== null || index === null || frame === undefined) {
    return (
      <>
        <h3 className={content.heading}>フレーム</h3>
        <EmptyState>{notice ?? NO_RECORD_NOTICE}</EmptyState>
      </>
    );
  }
  return <FrameContent view={view} index={index} frame={frame} />;
};

type FrameContentProps = { view: RunView; index: number; frame: TimedFrame };

/** 開いているフレームの中身 */
const FrameContent = ({ view, index, frame }: FrameContentProps) => {
  const { dispatch } = useViewState();
  const imageRef = useRef<HTMLDivElement>(null);
  const count = view.timedFrames.length;
  const time = formatSeconds(frame.t);
  useScrollIntoView(imageRef, String(index), "nearest");

  const distance = valleyDistance(frame);
  const rangeIndexes = indexRangeWithin(view.frameTimes, view.range);
  const rangeSummary = useMemo(
    () => summarizeFrames(view.frames.items, view.range),
    [view.frames.items, view.range],
  );
  const src = filePath(view.run, frame.thumb);

  return (
    <>
      <div className={content.headRow}>
        <h3 className={content.heading}>フレーム t={time}</h3>
        <span className={content.headMeta}>
          {(index + 1).toLocaleString("ja-JP")} / {count.toLocaleString("ja-JP")}
        </span>
        <div className={content.headActions}>
          <IconButton
            icon="prev"
            label="前のフレーム"
            disabledReason={index > 0 ? null : "最初のフレームを選んでいる"}
            onClick={() => dispatch({ type: "selectFrame", index: index - 1 })}
          />
          <IconButton
            icon="next"
            label="次のフレーム"
            disabledReason={index < count - 1 ? null : "最後のフレームを選んでいる"}
            onClick={() => dispatch({ type: "selectFrame", index: index + 1 })}
          />
        </div>
      </div>
      <div ref={imageRef} className={styles.mainImage}>
        <DetailImage key={src} src={src} alt={`t=${time} 秒のフレームの縮小画像`} />
      </div>
      <ol className={styles.neighbors} aria-label="前後のフレーム">
        {neighborFrameSlots(index, count, NEIGHBOR_RADIUS).map((slot) => (
          <NeighborFrame
            key={slot.offset}
            view={view}
            neighbor={slot.index}
            current={slot.offset === 0}
          />
        ))}
      </ol>
      <section className={content.block}>
        <h4 className={content.subheading}>このフレームを載せた要求</h4>
        <FrameRequests view={view} frame={frame} />
      </section>
      <dl className={content.facts}>
        <dt>鮮明度</dt>
        <dd className={content.data}>{formatDecimal(frame.sharpness, 1)}</dd>
        <dt>判定の状態</dt>
        <dd>{framePhaseText(frame.phase)}</dd>
        <dt>今の区間の山</dt>
        <dd className={content.data}>
          {frame.peak_t === null
            ? "—"
            : `t=${formatSeconds(frame.peak_t)} · ${formatDecimal(frame.peak_sharpness, 1)}`}
        </dd>
        <dt>谷とみなす値</dt>
        <dd className={content.data}>{formatDecimal(frame.ref, 1)}</dd>
        <dt>直前のフレームとの差分</dt>
        <dd className={content.data}>
          {formatDecimal(frame.diff, 2)} (似ているとみなす上限{" "}
          {formatDecimal(view.summary.similarity_threshold, 2)})
        </dd>
        <dt>{valleyDistanceLabel(distance)}</dt>
        <dd className={content.data}>{formatDecimal(distance.remaining, 1)}</dd>
      </dl>
      <section className={content.block}>
        <h4 className={content.subheading}>表示中の区間 ({describeRange(view.range)})</h4>
        <p className={content.note}>
          フレーム {(rangeIndexes.end - rangeIndexes.start).toLocaleString("ja-JP")} 枚 ·{" "}
          {describeFrameSummary(rangeSummary)}
        </p>
      </section>
    </>
  );
};

type NeighborFrameProps = { view: RunView; neighbor: number | null; current: boolean };

/** 前後の小さな画像の 1 枠。範囲の外の枠は空き */
const NeighborFrame = ({ view, neighbor, current }: NeighborFrameProps) => {
  const { dispatch } = useViewState();
  const frame = neighbor === null ? undefined : view.timedFrames[neighbor];
  if (neighbor === null || frame === undefined) {
    return <li aria-hidden="true" />;
  }
  const time = formatSeconds(frame.t);
  const src = filePath(view.run, frame.thumb);
  return (
    <li>
      <button
        type="button"
        className={styles.neighborButton}
        aria-current={current ? "true" : undefined}
        aria-label={`t=${time} 秒のフレーム`}
        onClick={() => dispatch({ type: "selectFrame", index: neighbor })}
      >
        <DetailImage key={src} src={src} alt="" />
        <span className={styles.neighborTime}>{time}</span>
      </button>
    </li>
  );
};

type FrameRequestsProps = { view: RunView; frame: TimedFrame };

/** フレームを載せた要求。フレームの時刻以後に送った要求を最大 FRAME_REQUEST_CANDIDATES 件読み、縮小画像が一致するものを出す */
const FrameRequests = ({ view, frame }: FrameRequestsProps) => {
  const { dispatch } = useViewState();
  const candidates = useMemo(
    () =>
      view.requests.available
        ? requestsAfter(view.requests.items, frame.t, FRAME_REQUEST_CANDIDATES)
        : [],
    [view.requests, frame.t],
  );
  const details = useRequestDetails(view.run, candidates);
  if (!view.requests.available) {
    return <EmptyState>{NO_REQUEST_RECORD_NOTICE}</EmptyState>;
  }
  if (details.error !== null) {
    return <ErrorState error={details.error} onRetry={details.retry} />;
  }
  if (details.pending) {
    return <Skeleton width="12rem" height="var(--control-height)" />;
  }
  const matched = requestsShowingFrame(details.details, frame.thumb);
  if (matched.length === 0) {
    return <EmptyState>載せた要求なし</EmptyState>;
  }
  return (
    <div className={content.buttonRow}>
      {matched.map((n) => (
        <Button
          key={n}
          aria-label={`要求 #${n} を要求のタブで開く`}
          onClick={() => dispatch({ type: "selectRequest", n })}
        >
          #{n}
        </Button>
      ))}
    </div>
  );
};
