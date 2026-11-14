// 選んでいる実行の summary と区間 API の結果から、画面全体で使う値 (フレームの時刻・中立に戻した時刻・
// 書き換えの一覧・選んでいる要求・再生位置・表示の区間) を求める純粋な関数。
import type {
  EventItem,
  FrameItem,
  PadItem,
  RangeResponse,
  RequestItem,
  RunSummary,
} from "../api/apiTypes.ts";
import { neutralTimesFromEvents } from "../controller/controllerTimeline.ts";
import { listRewrites, type NoteRewrite } from "../notes/noteHistory.ts";
import { playbackPosition, requestPosition } from "../requests/requestNavigation.ts";
import { computeViewWindow, type TimeRange, type ViewWidth } from "../timeline/viewWindow.ts";

/** t が null でないフレーム */
export type TimedFrame = FrameItem & { t: number };

/** buildRunData に渡す、summary と区間 API の応答 */
export type RunResponses = {
  summary: RunSummary;
  requests: RangeResponse<RequestItem>;
  frames: RangeResponse<FrameItem>;
  pad: RangeResponse<PadItem>;
  events: RangeResponse<EventItem>;
};

/**
 * 実行の記録から求めた、選択によらない値。
 * - requests・pad の items は API の応答の配列をそのまま持つ (controllerTimeline の関数は配列ごとに結果を覚える
 *   ため、同じ配列を渡し続ける)
 * - timedFrames は t が null でないフレームを t の昇順 (同じ t は API の順) に並べたもの。frameTimes はその t を
 *   同じ並びで持つ。ViewState の frameIndex は timedFrames の位置
 * - neutralTimes は neutralTimesFromEvents(events.items)、rewrites は listRewrites(summary.note_versions)
 * - tEnd は summary.t_end ?? 0
 */
export type RunData = RunResponses & {
  timedFrames: TimedFrame[];
  frameTimes: number[];
  neutralTimes: number[];
  rewrites: NoteRewrite[];
  tEnd: number;
  /** 要求の n から requests.items の位置を引く表 */
  requestIndexByN: ReadonlyMap<number, number>;
};

/** 選んでいる要求から求めた値 */
export type PlaybackSelection = {
  /** 選んでいる要求。要求の記録が無い (requests.available が false) ときと、selectedN の要求が無いときは null */
  selectedRequest: RequestItem | null;
  /** requests.items の順で selectedRequest の次の要求の再生位置 (requestPosition)。無ければ null */
  nextRequestPosition: number | null;
  /** 再生位置 (playbackPosition)。selectedRequest が null なら summary.t_end ?? 0 */
  position: number;
  /** タイムラインに表示する区間 (computeViewWindow) */
  range: TimeRange;
};

const hasTime = (frame: FrameItem): frame is TimedFrame => frame.t !== null;

/**
 * t が null でないフレームを t の昇順 (同じ t は配列の順) に並べ、その t を同じ並びで返す。
 * 渡した配列は変えない
 */
export const timedFramesOf = (
  frames: readonly FrameItem[],
): { timedFrames: TimedFrame[]; frameTimes: number[] } => {
  // Array.prototype.sort は安定なため、同じ t のフレームは配列の順のまま残る
  const timedFrames = frames.filter(hasTime).sort((left, right) => left.t - right.t);
  return { timedFrames, frameTimes: timedFrames.map((frame) => frame.t) };
};

/** 要求の n から配列の位置を引く表を作る (同じ n が複数あれば最初の位置) */
const indexByN = (requests: readonly RequestItem[]): Map<number, number> => {
  const indexes = new Map<number, number>();
  requests.forEach((request, index) => {
    if (!indexes.has(request.n)) {
      indexes.set(request.n, index);
    }
  });
  return indexes;
};

/** 実行の記録から、選択によらない値を求める (RunData を参照) */
export const buildRunData = (responses: RunResponses): RunData => ({
  ...responses,
  ...timedFramesOf(responses.frames.items),
  neutralTimes: neutralTimesFromEvents(responses.events.items),
  rewrites: listRewrites(responses.summary.note_versions),
  tEnd: responses.summary.t_end ?? 0,
  requestIndexByN: indexByN(responses.requests.items),
});

/**
 * 選んでいる要求の番号と表示する幅から、選んでいる要求・次の要求の再生位置・再生位置・表示の区間を返す。
 * 要求の記録が無い (requests.available が false) ときは、selectedN によらず selectedRequest を null にする
 */
export const selectPlayback = (
  data: RunData,
  selectedN: number | null,
  width: ViewWidth,
): PlaybackSelection => {
  const items = data.requests.items;
  const selectedIndex =
    data.requests.available && selectedN !== null ? data.requestIndexByN.get(selectedN) : undefined;
  const selectedRequest = selectedIndex === undefined ? null : (items[selectedIndex] ?? null);
  const nextRequest = selectedIndex === undefined ? undefined : items[selectedIndex + 1];
  const position = playbackPosition(selectedRequest, data.summary.t_end);
  return {
    selectedRequest,
    nextRequestPosition: nextRequest === undefined ? null : requestPosition(nextRequest),
    position,
    range: computeViewWindow(position, width, data.summary.t_end),
  };
};
