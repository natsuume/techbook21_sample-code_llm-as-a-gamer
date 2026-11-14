// タイムラインの区画。見出しの行 (表示中の区間・前後の要求・ヒートマップの展開・幅) の下に、目盛り・要求・操作に採った数
// (展開時はヒートマップ)・コントローラー入力・意味のあるフレーム・advisor とファイル・イベントのレーンを並べ、
// 再生位置の縦線を重ねる。下端に、カーソルを合わせた値の 1 行を置く。
import { useCallback, useId, useMemo, useRef } from "react";
import type { NoteKind } from "../../api/apiTypes.ts";
import { buildEventMarks } from "../../events/eventMarks.ts";
import { type RunView, useRunView } from "../../state/useRunView.ts";
import { useViewState } from "../../state/ViewStateContext.tsx";
import { advisorRowsOf } from "../../timeline/advisorRows.ts";
import { heatmapLayout } from "../../timeline/heatmapLayout.ts";
import {
  advisorLaneNotice,
  NO_RECORD_NOTICE,
  requestValueLaneNotice,
} from "../../timeline/laneNotice.ts";
import { buildRequestTimeIndex } from "../../timeline/requestWindow.ts";
import { describeRange } from "../../timeline/timelineTexts.ts";
import type { ViewWidth } from "../../timeline/viewWindow.ts";
import { Panel } from "../common/Panel.tsx";
import { AdoptedCountLane } from "./AdoptedCountLane.tsx";
import { AdvisorLane } from "./AdvisorLane.tsx";
import { AxisLane } from "./AxisLane.tsx";
import { ControllerInputLane } from "./ControllerInputLane.tsx";
import { EventLane } from "./EventLane.tsx";
import { FrameLane } from "./FrameLane.tsx";
import { HoverReadout, type HoverReadoutHandle } from "./HoverReadout.tsx";
import { Playhead } from "./Playhead.tsx";
import { QuestionHeatmapLane } from "./QuestionHeatmapLane.tsx";
import { RequestLane } from "./RequestLane.tsx";
import styles from "./Timeline.module.css";
import { TimelineControls } from "./TimelineControls.tsx";
import { TimelineSkeleton } from "./TimelineSkeleton.tsx";

/** 区画の見出し */
const TITLE = "タイムライン";

export type TimelineProps = {
  /** 枠に足す class (画面の配置で決める大きさとスクロール) */
  className?: string;
};

/** タイムラインの区画。選んでいる実行を読み込み中は、レーンと同じ大きさの骨組みを出す */
export const Timeline = ({ className }: TimelineProps) => {
  const runView = useRunView();
  if (runView.status !== "ready") {
    return <TimelineSkeleton title={TITLE} className={className} />;
  }
  return <TimelineContent view={runView.view} className={className} />;
};

type TimelineContentProps = {
  view: RunView;
  className?: string;
};

/** 読み込んだ実行のタイムライン */
const TimelineContent = ({ view, className }: TimelineContentProps) => {
  const { state, dispatch } = useViewState();
  const heatmapId = useId();
  const readoutRef = useRef<HoverReadoutHandle>(null);
  const { summary, range } = view;

  // 実行ごとに 1 回だけ作る値 (再生位置を動かしても作り直さない)
  const requestItems = view.requests.items;
  const timeIndex = useMemo(() => buildRequestTimeIndex(requestItems), [requestItems]);
  const layout = useMemo(() => heatmapLayout(summary.questions), [summary.questions]);
  const eventMarks = useMemo(
    () => buildEventMarks(view.events.items, view.tEnd),
    [view.events.items, view.tEnd],
  );
  const advisorRows = useMemo(
    () => advisorRowsOf(summary.advisor_runs, view.rewrites, view.tEnd),
    [summary.advisor_runs, view.rewrites, view.tEnd],
  );

  const showReadout = useCallback((text: string | null) => {
    readoutRef.current?.show(text);
  }, []);
  const selectRequest = useCallback(
    (n: number, question?: string) => dispatch({ type: "selectRequest", n, question }),
    [dispatch],
  );
  const selectFrame = useCallback(
    (index: number) => dispatch({ type: "selectFrame", index }),
    [dispatch],
  );
  const selectAdvisor = useCallback(
    (name: string) => dispatch({ type: "selectAdvisor", name }),
    [dispatch],
  );
  const selectRewrite = useCallback(
    (kind: NoteKind, version: number) =>
      dispatch({ type: "selectFile", file: { type: "note", kind, version } }),
    [dispatch],
  );
  const setWidth = useCallback(
    (width: ViewWidth) => dispatch({ type: "setWidth", width }),
    [dispatch],
  );
  const toggleHeatmap = useCallback(() => dispatch({ type: "toggleHeatmap" }), [dispatch]);

  const selectedN = view.selectedRequest?.n ?? null;
  const heatmapNotice =
    requestValueLaneNotice(summary.old_format, view.requests) ??
    (layout.rows.length === 0 ? NO_RECORD_NOTICE : null);

  return (
    <Panel
      title={TITLE}
      meta={<span className={styles.range}>{describeRange(range)}</span>}
      actions={
        <TimelineControls
          requests={view.requests}
          selectedN={selectedN}
          width={state.width}
          heatmapOpen={state.heatmapOpen}
          questionCount={summary.questions.length}
          heatmapId={heatmapId}
          onStep={selectRequest}
          onToggleHeatmap={toggleHeatmap}
          onWidthChange={setWidth}
        />
      }
      className={className}
    >
      <div className={styles.scroll}>
        <div className={styles.lanes}>
          <AxisLane range={range} />
          <RequestLane
            requests={view.requests}
            timeIndex={timeIndex}
            range={range}
            selectedN={selectedN}
            onSelect={selectRequest}
          />
          <AdoptedCountLane
            oldFormat={summary.old_format}
            requests={view.requests}
            timeIndex={timeIndex}
            range={range}
            onSelect={selectRequest}
            onHover={showReadout}
          />
          {state.heatmapOpen ? (
            <QuestionHeatmapLane
              id={heatmapId}
              notice={heatmapNotice}
              requests={view.requests}
              timeIndex={timeIndex}
              layout={layout}
              questionCount={summary.questions.length}
              advisorThreshold={summary.thresholds.advisor}
              range={range}
              onSelect={selectRequest}
              onHover={showReadout}
            />
          ) : null}
          <ControllerInputLane
            pad={view.pad}
            requests={view.requests}
            neutralTimes={view.neutralTimes}
            tEnd={view.tEnd}
            range={range}
            onHover={showReadout}
          />
          <FrameLane
            frames={view.frames}
            timedFrames={view.timedFrames}
            frameTimes={view.frameTimes}
            similarityThreshold={summary.similarity_threshold}
            range={range}
            onSelect={selectFrame}
            onHover={showReadout}
          />
          <AdvisorLane
            notice={advisorLaneNotice(summary.old_format, summary.advisor_runs, view.rewrites)}
            rows={advisorRows}
            range={range}
            selectedName={state.tab === "advisor" ? state.advisorName : null}
            onSelectAdvisor={selectAdvisor}
            onSelectRewrite={selectRewrite}
          />
          <EventLane events={view.events} marks={eventMarks} range={range} />
          <Playhead position={view.position} range={range} />
        </div>
      </div>
      <HoverReadout ref={readoutRef} />
    </Panel>
  );
};
