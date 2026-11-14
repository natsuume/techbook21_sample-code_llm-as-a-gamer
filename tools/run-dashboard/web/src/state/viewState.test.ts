// viewState のテスト。初めの状態と、操作ごとに変わる値・変わらない値を確かめる
import { describe, expect, it } from "vitest";
import { initialViewState, type ViewState, viewReducer } from "./viewState.ts";

// すべての値が初めの状態と違う状態
const busyState: ViewState = {
  run: "20260101-120000",
  selectedN: 7,
  width: 120,
  tab: "advisor",
  frameIndex: 4,
  focusQuestion: "A",
  advisorName: "advisor-tips-001",
  file: { type: "advisorFile", path: "advisor-tips-001/context.md" },
  fileView: "full",
  heatmapOpen: true,
  allQuestions: true,
};

describe("initialViewState", () => {
  it("画面を開いたときの値を持つ", () => {
    expect(initialViewState).toEqual({
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
    });
  });
});

describe("viewReducer", () => {
  it("selectRun は初めの状態に戻して run を入れる", () => {
    expect(viewReducer(busyState, { type: "selectRun", run: "20260202-090000" })).toEqual({
      ...initialViewState,
      run: "20260202-090000",
    });
  });

  it("runLoaded は最初の要求を選び、幅を 30 秒にする", () => {
    expect(viewReducer({ ...busyState, width: "all" }, { type: "runLoaded", firstN: 3 })).toEqual({
      ...busyState,
      selectedN: 3,
      width: 30,
    });
  });

  it('runLoaded の firstN が null なら、要求を選ばず幅を "all" にする', () => {
    expect(viewReducer(busyState, { type: "runLoaded", firstN: null })).toEqual({
      ...busyState,
      selectedN: null,
      width: "all",
    });
  });

  it("selectRequest は要求と問いを選び、要求のタブにする", () => {
    expect(viewReducer(busyState, { type: "selectRequest", n: 12, question: "UP" })).toEqual({
      ...busyState,
      selectedN: 12,
      focusQuestion: "UP",
      tab: "request",
    });
  });

  it("selectRequest に問いが無ければ focusQuestion を null にする", () => {
    expect(viewReducer(busyState, { type: "selectRequest", n: 12 })).toEqual({
      ...busyState,
      selectedN: 12,
      focusQuestion: null,
      tab: "request",
    });
  });

  it("selectFrame はフレームを選び、フレームのタブにする", () => {
    expect(viewReducer(busyState, { type: "selectFrame", index: 9 })).toEqual({
      ...busyState,
      frameIndex: 9,
      tab: "frame",
    });
  });

  it("selectAdvisor は advisor の実行を選び、advisor のタブにする", () => {
    expect(
      viewReducer(initialViewState, { type: "selectAdvisor", name: "advisor-controls-002" }),
    ).toEqual({
      ...initialViewState,
      advisorName: "advisor-controls-002",
      tab: "advisor",
    });
  });

  it("selectFile はファイルを選び、ファイルのタブにする", () => {
    const file = { type: "note", kind: "tips", version: 3 } as const;
    expect(viewReducer(busyState, { type: "selectFile", file })).toEqual({
      ...busyState,
      file,
      tab: "file",
    });
  });

  it("setTab はタブだけを変える", () => {
    expect(viewReducer(busyState, { type: "setTab", tab: "memo" })).toEqual({
      ...busyState,
      tab: "memo",
    });
  });

  it("setWidth は幅だけを変える", () => {
    expect(viewReducer(busyState, { type: "setWidth", width: 10 })).toEqual({
      ...busyState,
      width: 10,
    });
  });

  it("setFileView はファイルの表示だけを変える", () => {
    expect(viewReducer(busyState, { type: "setFileView", view: "diff" })).toEqual({
      ...busyState,
      fileView: "diff",
    });
  });

  it("toggleHeatmap はヒートマップの開閉を反転する", () => {
    expect(viewReducer(initialViewState, { type: "toggleHeatmap" })).toEqual({
      ...initialViewState,
      heatmapOpen: true,
    });
    expect(viewReducer(busyState, { type: "toggleHeatmap" })).toEqual({
      ...busyState,
      heatmapOpen: false,
    });
  });

  it("toggleAllQuestions は全問の表示を反転する", () => {
    expect(viewReducer(initialViewState, { type: "toggleAllQuestions" })).toEqual({
      ...initialViewState,
      allQuestions: true,
    });
    expect(viewReducer(busyState, { type: "toggleAllQuestions" })).toEqual({
      ...busyState,
      allQuestions: false,
    });
  });
});
