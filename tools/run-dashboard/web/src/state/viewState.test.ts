// viewState のテスト。初めの状態と、操作ごとに変わる値・変わらない値 (ライブの追従・一時停止・終わった実行の
// 表示ごとの違いと、詳細ペインで開く要求を含む) を確かめる
import { describe, expect, it } from "vitest";
import {
  type FileSelection,
  initialViewState,
  type SelectVia,
  type ViewAction,
  type ViewState,
  viewReducer,
} from "./viewState.ts";

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
  display: "live",
  follow: false,
  liveSession: "ended",
  endReason: "recordEnd",
  detailN: 5,
};

// ライブで最新を追っている状態 (追従中)
const followState: ViewState = {
  ...initialViewState,
  run: "20260101-120000",
  selectedN: 7,
  tab: "frame",
  frameIndex: 2,
  display: "live",
  follow: true,
  liveSession: "active",
  detailN: 4,
};
// ライブで一時停止している状態
const pausedState: ViewState = { ...followState, follow: false };
// つないだまま、終わった実行の表示にした状態
const replayState: ViewState = { ...followState, display: "replay" };
// end を受け取って、終わった実行の表示にした状態
const endedState: ViewState = {
  ...followState,
  display: "replay",
  liveSession: "ended",
  endReason: "stalled",
};

const noteFile: FileSelection = { type: "note", kind: "tips", version: 3 };

// フレーム・advisor の実行・ファイルを選ぶ操作と、その操作で変わる値
const selectOperations: [string, (via: SelectVia) => ViewAction, Partial<ViewState>][] = [
  [
    "selectFrame",
    (via) => ({ type: "selectFrame", index: 9, via }),
    { frameIndex: 9, tab: "frame" },
  ],
  [
    "selectAdvisor",
    (via) => ({ type: "selectAdvisor", name: "advisor-controls-002", via }),
    { advisorName: "advisor-controls-002", tab: "advisor" },
  ],
  [
    "selectFile",
    (via) => ({ type: "selectFile", file: noteFile, via }),
    { file: noteFile, tab: "file" },
  ],
];

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
      display: "replay",
      follow: true,
      liveSession: "none",
      endReason: null,
      detailN: null,
    });
  });
});

describe("viewReducer", () => {
  it("selectRun は初めの状態に戻して run を入れる", () => {
    expect(
      viewReducer(busyState, { type: "selectRun", run: "20260202-090000", inProgress: false }),
    ).toEqual({
      ...initialViewState,
      run: "20260202-090000",
    });
  });

  it("runLoaded は最初の要求を選び、幅を 30 秒にする", () => {
    expect(
      viewReducer(
        { ...busyState, display: "replay", width: "all" },
        { type: "runLoaded", firstN: 3 },
      ),
    ).toEqual({
      ...busyState,
      display: "replay",
      selectedN: 3,
      detailN: 3,
      width: 30,
    });
  });

  it('runLoaded の firstN が null なら、要求を選ばず幅を "all" にする', () => {
    expect(
      viewReducer({ ...busyState, display: "replay" }, { type: "runLoaded", firstN: null }),
    ).toEqual({
      ...busyState,
      display: "replay",
      selectedN: null,
      detailN: null,
      width: "all",
    });
  });

  it("selectRequest は要求と問いを選び、要求のタブにする", () => {
    expect(
      viewReducer(busyState, { type: "selectRequest", n: 12, question: "UP", via: "navigate" }),
    ).toEqual({
      ...busyState,
      selectedN: 12,
      detailN: 12,
      focusQuestion: "UP",
      tab: "request",
    });
  });

  it("selectRequest に問いが無ければ focusQuestion を null にする", () => {
    expect(viewReducer(busyState, { type: "selectRequest", n: 12, via: "navigate" })).toEqual({
      ...busyState,
      selectedN: 12,
      detailN: 12,
      focusQuestion: null,
      tab: "request",
    });
  });

  it("selectFrame はフレームを選び、フレームのタブにする", () => {
    expect(viewReducer(busyState, { type: "selectFrame", index: 9, via: "navigate" })).toEqual({
      ...busyState,
      frameIndex: 9,
      tab: "frame",
    });
  });

  it("selectAdvisor は advisor の実行を選び、advisor のタブにする", () => {
    expect(
      viewReducer(initialViewState, {
        type: "selectAdvisor",
        name: "advisor-controls-002",
        via: "navigate",
      }),
    ).toEqual({
      ...initialViewState,
      advisorName: "advisor-controls-002",
      tab: "advisor",
    });
  });

  it("selectFile はファイルを選び、ファイルのタブにする", () => {
    expect(viewReducer(busyState, { type: "selectFile", file: noteFile, via: "navigate" })).toEqual(
      {
        ...busyState,
        file: noteFile,
        tab: "file",
      },
    );
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

describe("viewReducer の実行の選択と読み込み", () => {
  it("selectRun は実行中の実行をライブで開き、つなぐ", () => {
    expect(
      viewReducer(busyState, { type: "selectRun", run: "20260202-090000", inProgress: true }),
    ).toEqual({
      ...initialViewState,
      run: "20260202-090000",
      display: "live",
      liveSession: "active",
    });
  });

  it("selectRun は実行中でない実行を終わった実行の表示で開き、つながない", () => {
    expect(
      viewReducer(followState, { type: "selectRun", run: "20260202-090000", inProgress: false }),
    ).toEqual({
      ...initialViewState,
      run: "20260202-090000",
      display: "replay",
      liveSession: "none",
    });
  });

  it("runLoaded は終わった実行の表示なら、詳細ペインで最初の要求を開く", () => {
    const loading: ViewState = { ...initialViewState, run: "r" };
    expect(viewReducer(loading, { type: "runLoaded", firstN: 3 })).toEqual({
      ...loading,
      selectedN: 3,
      detailN: 3,
      width: 30,
    });
  });

  it("runLoaded はライブなら、詳細ペインの要求を固定しない (detailN を null にする)", () => {
    const loading: ViewState = {
      ...initialViewState,
      run: "r",
      display: "live",
      liveSession: "active",
      detailN: 8,
    };
    expect(viewReducer(loading, { type: "runLoaded", firstN: 3 })).toEqual({
      ...loading,
      selectedN: 3,
      detailN: null,
      width: 30,
    });
    expect(viewReducer(loading, { type: "runLoaded", firstN: null })).toEqual({
      ...loading,
      selectedN: null,
      detailN: null,
      width: "all",
    });
  });
});

describe("viewReducer の選ぶ操作とライブの追従", () => {
  it("追従中に navigate で要求を選ぶと、一時停止にして再生位置と詳細ペインの要求を選ぶ", () => {
    expect(
      viewReducer(followState, { type: "selectRequest", n: 12, question: "UP", via: "navigate" }),
    ).toEqual({
      ...followState,
      follow: false,
      selectedN: 12,
      detailN: 12,
      focusQuestion: "UP",
      tab: "request",
    });
  });

  it("追従中に open で要求を選ぶと、詳細ペインの要求とタブだけを変える (追従は続ける)", () => {
    expect(
      viewReducer(followState, { type: "selectRequest", n: 12, question: "UP", via: "open" }),
    ).toEqual({
      ...followState,
      detailN: 12,
      focusQuestion: "UP",
      tab: "request",
    });
  });

  it.each([
    ["一時停止中", "navigate", pausedState],
    ["一時停止中", "open", pausedState],
    ["終わった実行の表示", "navigate", replayState],
    ["終わった実行の表示", "open", replayState],
  ] as const)("%s に %s で要求を選ぶと、再生位置と詳細ペインの要求を選ぶ", (_label, via, state) => {
    expect(viewReducer(state, { type: "selectRequest", n: 12, via })).toEqual({
      ...state,
      selectedN: 12,
      detailN: 12,
      focusQuestion: null,
      tab: "request",
    });
  });

  it.each(selectOperations)(
    "追従中の %s は、navigate なら一時停止にし、open なら追従を続ける",
    (_name, action, changed) => {
      expect(viewReducer(followState, action("navigate"))).toEqual({
        ...followState,
        ...changed,
        follow: false,
      });
      expect(viewReducer(followState, action("open"))).toEqual({ ...followState, ...changed });
    },
  );

  it.each(selectOperations)(
    "一時停止中と終わった実行の表示の %s は、follow を変えない",
    (_name, action, changed) => {
      for (const state of [pausedState, replayState]) {
        expect(viewReducer(state, action("navigate"))).toEqual({ ...state, ...changed });
        expect(viewReducer(state, action("open"))).toEqual({ ...state, ...changed });
      }
    },
  );
});

describe("viewReducer の followLatest", () => {
  it("追従中なら、再生位置の要求 selectedN を最新の要求にする (詳細ペインの要求とタブは変えない)", () => {
    expect(viewReducer(followState, { type: "followLatest", latestN: 20 })).toEqual({
      ...followState,
      selectedN: 20,
    });
  });

  it("追従中で要求がまだ無ければ、selectedN を null にする", () => {
    expect(viewReducer(followState, { type: "followLatest", latestN: null })).toEqual({
      ...followState,
      selectedN: null,
    });
  });

  it.each([
    ["一時停止中", pausedState],
    ["終わった実行の表示", replayState],
    ["end を受け取った後", endedState],
  ] as const)("%s なら変えない", (_label, state) => {
    expect(viewReducer(state, { type: "followLatest", latestN: 20 })).toEqual(state);
  });

  it.each(selectOperations)(
    "最新の要求を写した後に追従中の %s を navigate で選ぶと、一時停止中の再生位置は最新の要求のまま",
    (_name, action, changed) => {
      const followed = viewReducer(followState, { type: "followLatest", latestN: 20 });
      expect(viewReducer(followed, action("navigate"))).toEqual({
        ...followState,
        ...changed,
        selectedN: 20,
        follow: false,
      });
    },
  );
});

describe("viewReducer の resumeFollow", () => {
  it("一時停止中なら追従を再開し、選んでいた値とタブは変えない", () => {
    expect(viewReducer(pausedState, { type: "resumeFollow" })).toEqual({
      ...pausedState,
      follow: true,
    });
  });

  it("追従中と終わった実行の表示では変えない", () => {
    expect(viewReducer(followState, { type: "resumeFollow" })).toEqual(followState);
    expect(viewReducer(replayState, { type: "resumeFollow" })).toEqual(replayState);
  });
});

describe("viewReducer の setDisplay", () => {
  it("live: 終わった実行の表示でつないでいれば、ライブにして最新を追う", () => {
    expect(viewReducer(replayState, { type: "setDisplay", display: "live", latestN: 20 })).toEqual({
      ...replayState,
      display: "live",
      follow: true,
    });
  });

  it("live: 一時停止中なら最新を追う", () => {
    expect(viewReducer(pausedState, { type: "setDisplay", display: "live", latestN: 20 })).toEqual({
      ...pausedState,
      follow: true,
    });
  });

  it("live: つないでいない (none・ended) 実行と、追従中では変えない", () => {
    const notLive: ViewState = { ...initialViewState, run: "r", selectedN: 3, detailN: 3 };
    expect(viewReducer(notLive, { type: "setDisplay", display: "live", latestN: 20 })).toEqual(
      notLive,
    );
    expect(viewReducer(endedState, { type: "setDisplay", display: "live", latestN: 20 })).toEqual(
      endedState,
    );
    expect(viewReducer(followState, { type: "setDisplay", display: "live", latestN: 20 })).toEqual(
      followState,
    );
  });

  it("replay: 追従中なら、最新の要求を再生位置にして終わった実行の表示にする", () => {
    expect(
      viewReducer(followState, { type: "setDisplay", display: "replay", latestN: 20 }),
    ).toEqual({
      ...followState,
      display: "replay",
      selectedN: 20,
      follow: true,
    });
  });

  it("replay: 一時停止中なら、一時停止の選択のまま終わった実行の表示にする", () => {
    expect(
      viewReducer(pausedState, { type: "setDisplay", display: "replay", latestN: 20 }),
    ).toEqual({
      ...pausedState,
      display: "replay",
      follow: true,
    });
  });

  it("replay: 終わった実行の表示では変えない", () => {
    expect(
      viewReducer(replayState, { type: "setDisplay", display: "replay", latestN: 20 }),
    ).toEqual(replayState);
  });

  it("replay: 追従中でも latestN が null (読み込み中・要求なし) なら、selectedN は今の値のまま", () => {
    expect(
      viewReducer(followState, { type: "setDisplay", display: "replay", latestN: null }),
    ).toEqual({
      ...followState,
      display: "replay",
      follow: true,
    });
  });
});

describe("viewReducer の liveEnded と reopenLive", () => {
  it("追従中に end を受け取ると、最新の要求を再生位置にして終わった実行の表示にする", () => {
    expect(viewReducer(followState, { type: "liveEnded", recordEnd: true, latestN: 20 })).toEqual({
      ...followState,
      liveSession: "ended",
      endReason: "recordEnd",
      display: "replay",
      selectedN: 20,
      follow: true,
    });
  });

  it("追従中に end を受け取っても latestN が null なら、selectedN は今の値のまま", () => {
    expect(
      viewReducer(followState, { type: "liveEnded", recordEnd: false, latestN: null }),
    ).toEqual({
      ...followState,
      liveSession: "ended",
      endReason: "stalled",
      display: "replay",
      follow: true,
    });
  });

  it("一時停止中に end を受け取ると、一時停止の選択のまま終わった実行の表示にする", () => {
    expect(viewReducer(pausedState, { type: "liveEnded", recordEnd: false, latestN: 20 })).toEqual({
      ...pausedState,
      liveSession: "ended",
      endReason: "stalled",
      display: "replay",
      follow: true,
    });
  });

  it("終わった実行の表示で end を受け取ると、再生位置は変えない", () => {
    expect(viewReducer(replayState, { type: "liveEnded", recordEnd: true, latestN: 20 })).toEqual({
      ...replayState,
      liveSession: "ended",
      endReason: "recordEnd",
    });
    expect(viewReducer(replayState, { type: "liveEnded", recordEnd: false, latestN: 20 })).toEqual({
      ...replayState,
      liveSession: "ended",
      endReason: "stalled",
    });
  });

  it("reopenLive はつなぎ直してライブで最新を追う (選んでいた値は変えない)", () => {
    expect(viewReducer(endedState, { type: "reopenLive" })).toEqual({
      ...endedState,
      liveSession: "active",
      endReason: null,
      display: "live",
      follow: true,
    });
    const notLive: ViewState = { ...initialViewState, run: "r", selectedN: 3, detailN: 3 };
    expect(viewReducer(notLive, { type: "reopenLive" })).toEqual({
      ...notLive,
      liveSession: "active",
      display: "live",
      follow: true,
    });
  });
});

describe("viewReducer の詳細ペインの要求とライブの値", () => {
  it.each([
    ["setTab", { type: "setTab", tab: "request" }],
    ["setWidth", { type: "setWidth", width: 120 }],
    ["setFileView", { type: "setFileView", view: "full" }],
    ["toggleHeatmap", { type: "toggleHeatmap" }],
    ["toggleAllQuestions", { type: "toggleAllQuestions" }],
  ] as const)("%s は追従と詳細ペインの要求を変えない", (_name, action) => {
    const unpinned: ViewState = { ...followState, detailN: null };
    const next = viewReducer(unpinned, action);
    expect(next.selectedN).toBe(unpinned.selectedN);
    expect(next.display).toBe(unpinned.display);
    expect(next.follow).toBe(unpinned.follow);
    expect(next.liveSession).toBe(unpinned.liveSession);
    expect(next.endReason).toBe(unpinned.endReason);
    expect(next.detailN).toBeNull();
  });
});
