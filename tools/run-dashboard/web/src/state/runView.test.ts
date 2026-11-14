// runView のテスト。フレームの並べ方、選択によらない値、選んでいる要求からの再生位置と区間を確かめる
import { describe, expect, it } from "vitest";
import type {
  EventItem,
  FrameItem,
  PadItem,
  RangeResponse,
  RequestItem,
  RunSummary,
} from "../api/apiTypes.ts";
import { buildRunData, type RunResponses, selectPlayback, timedFramesOf } from "./runView.ts";

// テスト用のフレーム。指定しないキーは、判定の出来事の無い rising のフレームの値にする
const frame = (t: number | null, thumb: string): FrameItem => ({
  t,
  sharpness: 100,
  diff: null,
  phase: "rising",
  peak_t: null,
  peak_sharpness: 100,
  ref: 80,
  event: null,
  thumb,
});

// テスト用の要求。受信時刻は送信時刻の 0.5 秒後
const request = (
  n: number,
  tSent: number,
  tReceived: number | null = tSent + 0.5,
): RequestItem => ({
  n,
  t_sent: tSent,
  t_received: tReceived,
  result: tReceived === null ? "error" : "ok",
  action: null,
  advisor_requested: [],
  advisor_accepted: [],
  p: null,
  adopted_count: null,
});

// テスト用の summary。指定しないキーは、advisor と書き換えの無い長さ 60 秒の実行の値にする
const summary = (fields: Partial<RunSummary> = {}): RunSummary => ({
  run: "r",
  game: "g",
  model: "m",
  started: null,
  args: null,
  thresholds: { advisor: 0.5 },
  thresholds_assumed: false,
  valley_ratio: 0.5,
  similarity_threshold: 0.15,
  questions: [],
  old_format: false,
  t_end: 60,
  counts: { requests: 0, frames: 0, pad: 0 },
  decide_s_median: null,
  advisor_runs: [],
  note_versions: [],
  recorded: { dashboard: true, requests: true, advisor_steps: false },
  dropped: null,
  skipped_lines: {},
  ...fields,
});

const range = <T>(items: T[], available = true): RangeResponse<T> => ({ available, items });

const responses = (fields: Partial<RunResponses> = {}): RunResponses => ({
  summary: summary(),
  requests: range<RequestItem>([]),
  frames: range<FrameItem>([]),
  pad: range<PadItem>([]),
  events: range<EventItem>([]),
  ...fields,
});

describe("timedFramesOf", () => {
  it("t が null のフレームを除き、t の昇順に並べて t の配列を同じ並びで返す", () => {
    const frames = [frame(2, "c"), frame(null, "x"), frame(0.5, "a"), frame(1, "b")];
    const { timedFrames, frameTimes } = timedFramesOf(frames);
    expect(timedFrames.map((item) => item.thumb)).toEqual(["a", "b", "c"]);
    expect(frameTimes).toEqual([0.5, 1, 2]);
  });

  it("同じ t のフレームは配列の順のまま残し、渡した配列は変えない", () => {
    const frames = [frame(1, "b1"), frame(0, "a"), frame(1, "b2")];
    const { timedFrames } = timedFramesOf(frames);
    expect(timedFrames.map((item) => item.thumb)).toEqual(["a", "b1", "b2"]);
    expect(frames.map((item) => item.thumb)).toEqual(["b1", "a", "b2"]);
  });
});

describe("buildRunData", () => {
  it("区間 API の items を同じ配列のまま持つ", () => {
    const requests = range([request(1, 1)]);
    const pad = range<PadItem>([{ t: 1, source: "apply", state: "A", ok: true, error: null }]);
    const data = buildRunData(responses({ requests, pad }));
    expect(data.requests.items).toBe(requests.items);
    expect(data.pad.items).toBe(pad.items);
  });

  it("中立に戻した時刻・書き換えの一覧・tEnd を求める", () => {
    const data = buildRunData(
      responses({
        summary: summary({
          t_end: null,
          note_versions: [
            {
              version: 1,
              t: 5,
              request: 2,
              initial: false,
              changed: [{ kind: "tips", by: "external" }],
            },
            { version: 0, t: 0, request: 1, initial: true, changed: [] },
          ],
        }),
        events: range<EventItem>([
          { t: 9, event: "capture_stopped" },
          { t: 3, event: "decision_error" },
          { t: 4, event: "start" },
        ]),
      }),
    );
    expect(data.neutralTimes).toEqual([3, 9]);
    expect(data.rewrites).toEqual([
      { version: 1, t: 5, request: 2, kind: "tips", by: "external", external: true },
    ]);
    expect(data.tEnd).toBe(0);
  });
});

describe("selectPlayback", () => {
  const requests = range([request(1, 1), request(2, 2, null), request(3, 3)]);

  it("selectedN の要求と、配列の次の要求の再生位置を返す", () => {
    const data = buildRunData(responses({ requests }));
    const selection = selectPlayback(data, 1, 30);
    expect(selection.selectedRequest?.n).toBe(1);
    expect(selection.position).toBe(1.5);
    // 次の要求 #2 は失敗した要求のため、再生位置は送信時刻
    expect(selection.nextRequestPosition).toBe(2);
    expect(selection.range).toEqual({ from: 0, to: 30 });
  });

  it("最後の要求の次の要求の再生位置は null", () => {
    const data = buildRunData(responses({ requests }));
    expect(selectPlayback(data, 3, 30).nextRequestPosition).toBeNull();
  });

  it("selectedN の要求が無ければ、選んでいる要求は null で再生位置は t_end", () => {
    const data = buildRunData(responses({ requests }));
    const selection = selectPlayback(data, 99, "all");
    expect(selection.selectedRequest).toBeNull();
    expect(selection.nextRequestPosition).toBeNull();
    expect(selection.position).toBe(60);
    expect(selection.range).toEqual({ from: 0, to: 60 });
  });

  it("要求の記録が無ければ selectedN によらず選んでいる要求は null で、再生位置は t_end ?? 0", () => {
    const unavailable = range([request(1, 1)], false);
    const data = buildRunData(
      responses({ requests: unavailable, summary: summary({ t_end: null }) }),
    );
    const selection = selectPlayback(data, 1, "all");
    expect(selection.selectedRequest).toBeNull();
    expect(selection.position).toBe(0);
    expect(selection.range).toEqual({ from: 0, to: 1 });
  });

  it("表示の区間は再生位置を中心にする", () => {
    const data = buildRunData(responses({ requests: range([request(1, 30)]) }));
    expect(selectPlayback(data, 1, 10).range).toEqual({ from: 25.5, to: 35.5 });
  });
});
