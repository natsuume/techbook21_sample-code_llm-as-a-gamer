// 画面の選択の状態 (選んでいる実行・要求・表示する幅・詳細ペインのタブ・ライブの追従など) と、それを変える reducer。
// 再生位置は状態に持たず、選んでいる要求から playbackPosition で求める。
import type { NoteKind } from "../api/apiTypes.ts";
import type { ViewWidth } from "../timeline/viewWindow.ts";

export type DetailTab = "request" | "frame" | "advisor" | "file" | "memo";
/** 表示。live は実行中の記録を追う表示、replay は終わった実行の再生の表示 */
export type DisplayMode = "live" | "replay";
/** サーバー送信イベント (/live) の接続。none はつながない、active はつなぐ、ended は end を受け取った */
export type LiveSession = "none" | "active" | "ended";
/** end を受け取った理由。recordEnd は終了の記録があった、stalled は記録が 10 秒止まった */
export type LiveEndReason = "recordEnd" | "stalled";
/**
 * 選ぶ操作の出どころ。navigate はタイムライン・上の段・前後の移動 (ライブの追従中なら一時停止にする)、
 * open は詳細ペインで開くだけ (追従を止めない)
 */
export type SelectVia = "navigate" | "open";
/** ファイルのタブで開くもの。ゲームの情報の版か、advisor の入出力のファイル */
export type FileSelection =
  | { type: "note"; kind: NoteKind; version: number }
  | { type: "advisorFile"; path: string };
/**
 * 画面の選択の状態。
 * - selectedN: 再生位置の要求。ライブで最新を追っている間は使わない (最後の要求を選ぶ)
 * - display: ライブ (live) か終わった実行 (replay) の表示
 * - follow: display が live のとき、最新を追っているか (false は一時停止)
 * - liveSession: /live をつなぐか (active)、つながないか (none)、end を受け取ったか (ended)
 * - endReason: end を受け取った理由 (受け取っていなければ null)
 * - detailN: 詳細ペインの要求のタブで開く要求 (再生位置の selectedN と別)。null の間は、表示中の選択の要求
 *   (最新を追っている間は最後の要求) を出す
 */
export type ViewState = {
  run: string | null;
  selectedN: number | null;
  width: ViewWidth;
  tab: DetailTab;
  frameIndex: number | null;
  focusQuestion: string | null;
  advisorName: string | null;
  file: FileSelection | null;
  fileView: "diff" | "full";
  heatmapOpen: boolean;
  allQuestions: boolean;
  display: DisplayMode;
  follow: boolean;
  liveSession: LiveSession;
  endReason: LiveEndReason | null;
  detailN: number | null;
};
/**
 * 状態を変える操作。
 * - selectRun の inProgress は、実行の一覧のその実行の in_progress
 * - via は選ぶ操作の出どころ (SelectVia)
 * - setDisplay・liveEnded の latestN は、表示中のデータの最後の要求の n (latestRequestN)
 */
export type ViewAction =
  | { type: "selectRun"; run: string; inProgress: boolean }
  | { type: "runLoaded"; firstN: number | null }
  | { type: "selectRequest"; n: number; question?: string; via: SelectVia }
  | { type: "selectFrame"; index: number; via: SelectVia }
  | { type: "selectAdvisor"; name: string; via: SelectVia }
  | { type: "selectFile"; file: FileSelection; via: SelectVia }
  | { type: "resumeFollow" }
  | { type: "setDisplay"; display: DisplayMode; latestN: number | null }
  | { type: "liveEnded"; recordEnd: boolean; latestN: number | null }
  | { type: "reopenLive" }
  | { type: "setTab"; tab: DetailTab }
  | { type: "setWidth"; width: ViewWidth }
  | { type: "setFileView"; view: "diff" | "full" }
  | { type: "toggleHeatmap" }
  | { type: "toggleAllQuestions" };

/** 画面を開いたときの状態 */
export const initialViewState: ViewState = {
  run: null,
  selectedN: null,
  width: 30,
  tab: "request",
  frameIndex: null,
  focusQuestion: null,
  advisorName: null,
  file: null,
  fileView: "diff",
  heatmapOpen: false,
  allQuestions: false,
  display: "replay",
  follow: true,
  liveSession: "none",
  endReason: null,
  detailN: null,
};

/**
 * 操作に応じて次の状態を返す。「追従中」は display が live で follow、「一時停止中」は display が live で
 * follow でない状態。
 * - selectRun: initialViewState に戻して run を入れる。inProgress なら display live・liveSession active、
 *   そうでなければ display replay・liveSession none
 * - runLoaded: selectedN = firstN。replay なら detailN = firstN、live なら detailN = null (null の間、詳細ペインの
 *   要求のタブは最新の要求を追う)。firstN が null なら width を "all"、そうでなければ 30
 * - selectRequest: selectedN と detailN を n、focusQuestion を question (無ければ null)、tab を "request"。
 *   追従中の navigate は follow も false (一時停止) にする。追従中の open は detailN・focusQuestion・tab だけを
 *   変える (selectedN と follow は変えない)
 * - selectFrame は frameIndex と tab "frame"、selectAdvisor は advisorName と tab "advisor"、selectFile は file と
 *   tab "file"。追従中の navigate は follow も false にする
 * - resumeFollow: 一時停止中なら follow を true にする (selectedN・detailN・tab・frameIndex は変えない)。
 *   それ以外は変えない
 * - setDisplay live: replay で liveSession が active なら display live・follow true、一時停止中なら follow true。
 *   それ以外は変えない
 * - setDisplay replay: 追従中なら display replay・selectedN = latestN (再生位置を動かさない)・follow true、
 *   一時停止中なら display replay・follow true (selectedN は一時停止の選択のまま)。replay なら変えない
 * - liveEnded: liveSession ended・endReason (recordEnd なら "recordEnd"、そうでなければ "stalled")。
 *   追従中は display replay・selectedN = latestN・follow true も、一時停止中は display replay・follow true も変える
 * - reopenLive: liveSession active・endReason null・display live・follow true
 * - setTab・setWidth・setFileView はその値だけを変える。toggleHeatmap・toggleAllQuestions は反転する
 * - detailN を変えるのは selectRun・runLoaded・selectRequest だけ (タブを切り替えるだけでは開く要求を固定しない)
 */
export const viewReducer = (state: ViewState, action: ViewAction): ViewState => {
  switch (action.type) {
    case "selectRun":
      return { ...initialViewState, run: action.run };
    case "runLoaded":
      return {
        ...state,
        selectedN: action.firstN,
        width: action.firstN === null ? "all" : 30,
      };
    case "selectRequest":
      return {
        ...state,
        selectedN: action.n,
        focusQuestion: action.question ?? null,
        tab: "request",
      };
    case "selectFrame":
      return { ...state, frameIndex: action.index, tab: "frame" };
    case "selectAdvisor":
      return { ...state, advisorName: action.name, tab: "advisor" };
    case "selectFile":
      return { ...state, file: action.file, tab: "file" };
    case "resumeFollow":
    case "setDisplay":
    case "liveEnded":
    case "reopenLive":
      throw new Error("not implemented");
    case "setTab":
      return { ...state, tab: action.tab };
    case "setWidth":
      return { ...state, width: action.width };
    case "setFileView":
      return { ...state, fileView: action.view };
    case "toggleHeatmap":
      return { ...state, heatmapOpen: !state.heatmapOpen };
    case "toggleAllQuestions":
      return { ...state, allQuestions: !state.allQuestions };
  }
};
