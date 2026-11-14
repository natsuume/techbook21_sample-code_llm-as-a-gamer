// 詳細ペインの要求のタブ。選んでいる要求の時刻・決めた操作・載せた画像・問いの答え・所要時間の内訳・ゲームの情報の版・
// 直近の応答と送った操作・前回の操作を出し、前後の要求へ移るボタンを置く。
import { useMemo, useRef } from "react";
import { filePath } from "../../api/apiPaths.ts";
import type { RequestDetail, RequestImage, RequestItem } from "../../api/apiTypes.ts";
import { useRequestDetail } from "../../api/queries.ts";
import { toQueryView } from "../../api/queryView.ts";
import { isOldFormatRequestDetail } from "../../api/requestDetailFormat.ts";
import { NO_REQUESTS_NOTICE, requestTabNotice } from "../../detail/detailNotices.ts";
import { noteSelectionForVersion, noteVersionText } from "../../detail/noteFiles.ts";
import { questionAnswerRows, TOP_ANSWER_COUNT } from "../../detail/questionAnswers.ts";
import { requestTimeText } from "../../detail/requestFacts.ts";
import { formatMilliseconds, timingBreakdown } from "../../detail/timingBreakdown.ts";
import { formatProbability, ratioToPercent } from "../../format/formatProbability.ts";
import { formatBytes } from "../../format/formatValues.ts";
import { frameIndexOfImage } from "../../frames/frameMetrics.ts";
import type { RunView } from "../../state/useRunView.ts";
import { useViewState } from "../../state/ViewStateContext.tsx";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import { OLD_FORMAT_NOTICE } from "../../timeline/laneNotice.ts";
import { requestStep } from "../../timeline/requestStep.ts";
import { Button } from "../common/Button.tsx";
import { Chip } from "../common/Chip.tsx";
import { EmptyState } from "../common/EmptyState.tsx";
import { ErrorState } from "../common/ErrorState.tsx";
import { IconButton } from "../common/IconButton.tsx";
import { SegmentedControl } from "../common/SegmentedControl.tsx";
import { Skeleton } from "../common/Skeleton.tsx";
import content from "./DetailContent.module.css";
import { DetailImage } from "./DetailImage.tsx";
import { QuestionAnswerList } from "./QuestionAnswerList.tsx";
import styles from "./RequestTab.module.css";
import { useScrollIntoView } from "./useScrollIntoView.ts";

/** 問いの答えの件数の切り替え。top は上位 TOP_ANSWER_COUNT 件、all はすべて */
type AnswerScope = "top" | "all";

/** 要求のタブ。要求を選べない実行では、理由の文言だけを出す */
export const RequestTab = ({ view }: { view: RunView }) => {
  const request = view.selectedRequest;
  const notice = requestTabNotice(view.summary.old_format, view.requests, request !== null);
  if (notice !== null || request === null) {
    return (
      <>
        <h3 className={content.heading}>要求</h3>
        <EmptyState>{notice ?? NO_REQUESTS_NOTICE}</EmptyState>
      </>
    );
  }
  return <RequestContent view={view} request={request} />;
};

type RequestContentProps = { view: RunView; request: RequestItem };

/** 選んでいる要求の中身。要求の詳細 (画像・問いの答え・所要時間・文脈) は要求の詳細の API から読む */
const RequestContent = ({ view, request }: RequestContentProps) => {
  const { dispatch } = useViewState();
  const detailView = toQueryView(useRequestDetail(view.run, request.n));
  const previous = requestStep(view.requests, request.n, -1);
  const next = requestStep(view.requests, request.n, 1);

  return (
    <>
      <div className={content.headRow}>
        <h3 className={content.heading}>要求 #{request.n}</h3>
        <Chip>{request.result}</Chip>
        <div className={content.headActions}>
          <IconButton
            icon="prev"
            label="前の要求"
            disabledReason={previous.reason}
            onClick={() => {
              if (previous.n !== null) {
                dispatch({ type: "selectRequest", n: previous.n });
              }
            }}
          />
          <IconButton
            icon="next"
            label="次の要求"
            disabledReason={next.reason}
            onClick={() => {
              if (next.n !== null) {
                dispatch({ type: "selectRequest", n: next.n });
              }
            }}
          />
        </div>
      </div>
      <dl className={content.facts}>
        <dt>時刻</dt>
        <dd className={content.data}>{requestTimeText(request)}</dd>
        <dt>決めた操作</dt>
        <dd className={content.data}>{request.action ?? "—"}</dd>
      </dl>
      {detailView.state === "loading" || detailView.state === "idle" ? (
        <RequestDetailSkeleton />
      ) : null}
      {detailView.state === "error" ? (
        <ErrorState error={detailView.error} onRetry={detailView.retry} />
      ) : null}
      {detailView.state === "ready" && isOldFormatRequestDetail(detailView.data) ? (
        <EmptyState>{OLD_FORMAT_NOTICE}</EmptyState>
      ) : null}
      {detailView.state === "ready" && !isOldFormatRequestDetail(detailView.data) ? (
        <RequestDetailBlocks view={view} detail={detailView.data} />
      ) : null}
    </>
  );
};

/** 要求の詳細を読み込み中の骨組み (画像・問いの答え・所要時間・文脈の区画) */
const RequestDetailSkeleton = () => (
  <>
    <Skeleton height="6rem" />
    <Skeleton height="14rem" />
    <Skeleton height="4rem" />
    <Skeleton height="6rem" />
  </>
);

type RequestDetailBlocksProps = { view: RunView; detail: RequestDetail };

/** 要求の詳細の区画 */
const RequestDetailBlocks = ({ view, detail }: RequestDetailBlocksProps) => {
  const { dispatch } = useViewState();
  const noteVersions = view.summary.note_versions;
  const noteVersion = detail.note_version;
  const versionExists =
    noteVersion !== null && noteVersions.some((item) => item.version === noteVersion);
  return (
    <>
      <section className={content.block}>
        <h4 className={content.subheading}>
          載せた画像 ({detail.images.length.toLocaleString("ja-JP")} 枚)
        </h4>
        <RequestImages view={view} images={detail.images} />
      </section>
      <QuestionAnswers view={view} detail={detail} />
      <TimingBlock timings={detail.timings} />
      <section className={content.block}>
        <h4 className={content.subheading}>ゲームの情報の版</h4>
        {versionExists && noteVersion !== null ? (
          <div className={content.buttonRow}>
            <Button
              className={content.wrapButton}
              aria-label={`${noteVersionText(noteVersions, noteVersion)} をファイルのタブで開く`}
              onClick={() =>
                dispatch({
                  type: "selectFile",
                  file: noteSelectionForVersion(noteVersions, noteVersion),
                })
              }
            >
              {noteVersionText(noteVersions, noteVersion)}
            </Button>
          </div>
        ) : (
          <p className={joinClassNames(content.note, content.data)}>
            {noteVersionText(noteVersions, noteVersion)}
          </p>
        )}
      </section>
      <section className={content.block}>
        <h4 className={content.subheading}>送った文脈</h4>
        <details className={content.textBox} open>
          <summary>直近の応答と送った操作</summary>
          <pre className={content.pre}>{detail.history_text}</pre>
        </details>
        <div className={content.textBox}>
          <h5 className={content.subheading}>前回の操作</h5>
          <pre className={content.pre}>{detail.previous_operation_text ?? "(なし)"}</pre>
        </div>
      </section>
    </>
  );
};

type RequestImagesProps = { view: RunView; images: readonly RequestImage[] };

/** 載せた画像の縮小画像。フレームに当たる画像は押すとフレームのタブで開く */
const RequestImages = ({ view, images }: RequestImagesProps) => {
  const { dispatch } = useViewState();
  if (images.length === 0) {
    return <EmptyState>画像なし</EmptyState>;
  }
  return (
    <ul className={styles.thumbs}>
      {images.map((image) => {
        const index = frameIndexOfImage(view.timedFrames, view.frameTimes, image);
        const src = image.thumb === null ? null : filePath(view.run, image.thumb);
        const picture = <DetailImage key={src} src={src} alt="" emptyText="縮小画像の記録なし" />;
        return (
          <li key={`${image.t}:${image.label}`}>
            <figure className={styles.figure}>
              {index >= 0 ? (
                <button
                  type="button"
                  className={styles.thumbButton}
                  aria-label={`${image.label} をフレームのタブで開く`}
                  onClick={() => dispatch({ type: "selectFrame", index })}
                >
                  {picture}
                </button>
              ) : (
                picture
              )}
              <figcaption className={styles.caption}>
                {image.label}
                <span className={styles.captionMeta}>
                  {formatBytes(image.bytes)}
                  {index >= 0 ? "" : " · フレームの記録なし"}
                </span>
              </figcaption>
            </figure>
          </li>
        );
      })}
    </ul>
  );
};

type QuestionAnswersProps = { view: RunView; detail: RequestDetail };

/** 問いの答えの区画。上位の件数とすべての切り替え、focusQuestion の問いの強調とスクロール */
const QuestionAnswers = ({ view, detail }: QuestionAnswersProps) => {
  const { state, dispatch } = useViewState();
  const focusedRowRef = useRef<HTMLLIElement>(null);
  const questions = view.summary.questions;
  const threshold = view.summary.thresholds.advisor;
  const scope: AnswerScope = state.allQuestions ? "all" : "top";
  const rows = useMemo(
    () =>
      questionAnswerRows(questions, detail, {
        limit: scope === "all" ? null : TOP_ANSWER_COUNT,
        focusQuestion: state.focusQuestion,
        advisorThreshold: threshold,
      }),
    [questions, detail, scope, state.focusQuestion, threshold],
  );
  const focusedName = rows.find((row) => row.focused)?.name ?? null;
  useScrollIntoView(
    focusedRowRef,
    focusedName === null ? null : `${detail.n}:${focusedName}`,
    "center",
  );
  return (
    <section className={content.block}>
      <div className={content.headRow}>
        <h4 className={content.subheading}>
          問いの答え (advisor のしきい値 {formatProbability(threshold)})
        </h4>
        <SegmentedControl<AnswerScope>
          label="問いの答えの件数"
          options={[
            { value: "top", label: `上位 ${TOP_ANSWER_COUNT} 件` },
            { value: "all", label: `${questions.length.toLocaleString("ja-JP")} 件` },
          ]}
          value={scope}
          onChange={(value) => {
            if (value !== scope) {
              dispatch({ type: "toggleAllQuestions" });
            }
          }}
        />
      </div>
      {rows.length === 0 ? (
        <EmptyState>問いの値なし</EmptyState>
      ) : (
        <QuestionAnswerList rows={rows} focusedRowRef={focusedRowRef} label="問いの答え" />
      )}
    </section>
  );
};

/** 所要時間の内訳の区画。4 区間の積み上げ棒と、区間ごとの ms の表 */
const TimingBlock = ({ timings }: { timings: RequestDetail["timings"] }) => {
  const breakdown = timingBreakdown(timings);
  return (
    <section className={content.block}>
      <h4 className={content.subheading}>
        所要時間の内訳 (合計 {formatMilliseconds(breakdown.totalSeconds)})
      </h4>
      <div className={styles.timingBar} aria-hidden="true">
        {breakdown.segments.map((segment) => (
          <span
            key={segment.key}
            className={joinClassNames(styles.timingSegment, styles[segment.key])}
            style={{ left: ratioToPercent(segment.start), width: ratioToPercent(segment.width) }}
          />
        ))}
      </div>
      <dl className={content.facts}>
        {breakdown.segments.map((segment) => (
          <div key={segment.key} className={styles.timingRow}>
            <dt>
              <span
                className={joinClassNames(styles.swatch, styles[segment.key])}
                aria-hidden="true"
              />
              {segment.label}
            </dt>
            <dd className={content.data}>{formatMilliseconds(segment.seconds)}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
};
