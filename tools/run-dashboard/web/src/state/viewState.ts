// 画面の選択の状態 (選んでいる実行・要求・表示する幅・詳細ペインのタブなど) と、それを変える reducer。
// 再生位置は状態に持たず、選んでいる要求から playbackPosition で求める。
import type { NoteKind } from "../api/apiTypes.ts";
import type { ViewWidth } from "../timeline/viewWindow.ts";

export type DetailTab = "request" | "frame" | "advisor" | "file" | "memo";
/** ファイルのタブで開くもの。ゲームの情報の版か、advisor の入出力のファイル */
export type FileSelection =
  | { type: "note"; kind: NoteKind; version: number }
  | { type: "advisorFile"; path: string };
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
};
export type ViewAction =
  | { type: "selectRun"; run: string }
  | { type: "runLoaded"; firstN: number | null }
  | { type: "selectRequest"; n: number; question?: string }
  | { type: "selectFrame"; index: number }
  | { type: "selectAdvisor"; name: string }
  | { type: "selectFile"; file: FileSelection }
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
};

/**
 * 操作に応じて次の状態を返す。
 * - selectRun: initialViewState に戻して run を入れる
 * - runLoaded: selectedN = firstN。firstN が null なら width を "all"、そうでなければ 30
 * - selectRequest: selectedN を n、focusQuestion を question (無ければ null)、tab を "request"
 * - selectFrame: frameIndex と tab "frame"。selectAdvisor: advisorName と tab "advisor"。selectFile: file と tab "file"
 * - setTab・setWidth・setFileView はその値だけを変える。toggleHeatmap・toggleAllQuestions は反転する
 */
export const viewReducer: (state: ViewState, action: ViewAction) => ViewState = () => {
  throw new Error("not implemented");
};
