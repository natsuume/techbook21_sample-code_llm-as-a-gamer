// liveQueryCache のテスト。本物の QueryClient のキャッシュに、届いたイベントを足すこと・イベントで足した記録の有無・
// つなぐ位置・advisor の実行の詳細への step の足し方・読み直すキャッシュの無効化を確かめる
import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import type {
  AdvisorDetail,
  EventItem,
  FrameItem,
  PadItem,
  RangeResponse,
  RequestItem,
  RunSummary,
} from "../api/apiTypes.ts";
import {
  advisorQueryKey,
  rangeQueryKey,
  requestQueryKey,
  summaryQueryKey,
} from "../api/queries.ts";
import { hasAppendedRecords, liveCursorsOf, writeLiveEvents } from "./liveQueryCache.ts";

const RUN = "20260101-120000";

const request = (n: number): RequestItem => ({
  n,
  t_sent: n,
  t_received: n + 0.5,
  result: "ok",
  action: null,
  advisor_requested: [],
  advisor_accepted: [],
  p: null,
  adopted_count: null,
});

const range = <T>(items: T[], cursor: number): RangeResponse<T> => ({
  available: true,
  items,
  cursor,
});

const summary: RunSummary = {
  run: RUN,
  game: "g",
  model: "m",
  started: null,
  in_progress: true,
  args: null,
  thresholds: { advisor: 0.5 },
  thresholds_assumed: false,
  valley_ratio: 0.5,
  similarity_threshold: 0.15,
  questions: [],
  old_format: false,
  t_end: 10,
  counts: { requests: 1, frames: 0, pad: 0 },
  decide_s_median: null,
  advisor_runs: [],
  note_versions: [],
  recorded: { dashboard: true, requests: true, advisor_steps: true },
  dropped: null,
  skipped_lines: {},
};

/** サーバーから読んだ値を入れたキャッシュ (requests の cursor 100、frames 200、pad 300、events 400) */
const seededClient = (): QueryClient => {
  const queryClient = new QueryClient();
  queryClient.setQueryData(summaryQueryKey(RUN), summary);
  queryClient.setQueryData(rangeQueryKey(RUN, "requests"), range([request(1)], 100));
  queryClient.setQueryData(rangeQueryKey(RUN, "frames"), range<FrameItem>([], 200));
  queryClient.setQueryData(rangeQueryKey(RUN, "pad"), range<PadItem>([], 300));
  queryClient.setQueryData(rangeQueryKey(RUN, "events"), range<EventItem>([], 400));
  return queryClient;
};

describe("liveCursorsOf", () => {
  it("区間 API のキャッシュの cursor を返し、events の cursor を run に入れる", () => {
    expect(liveCursorsOf(seededClient(), RUN)).toEqual({
      run: 400,
      requests: 100,
      frames: 200,
      pad: 300,
    });
  });

  it("キャッシュに無い種類は null", () => {
    expect(liveCursorsOf(new QueryClient(), RUN)).toEqual({
      run: null,
      requests: null,
      frames: null,
      pad: null,
    });
  });
});

describe("writeLiveEvents と hasAppendedRecords", () => {
  it("サーバーから読んだだけのキャッシュには、イベントで足した記録が無い", () => {
    expect(hasAppendedRecords(seededClient(), RUN)).toBe(false);
  });

  it("イベントを足すとキャッシュの items が伸び (cursor は変えない)、足した記録があることになる", () => {
    const queryClient = seededClient();
    writeLiveEvents(queryClient, RUN, [{ kind: "request", data: request(2) }]);
    const requests = queryClient.getQueryData<RangeResponse<RequestItem>>(
      rangeQueryKey(RUN, "requests"),
    );
    expect(requests?.items.map((item) => item.n)).toEqual([1, 2]);
    expect(requests?.cursor).toBe(100);
    expect(hasAppendedRecords(queryClient, RUN)).toBe(true);
  });

  it("サーバーから読み直した値に替わると、足した記録は無いことになる", () => {
    const queryClient = seededClient();
    writeLiveEvents(queryClient, RUN, [{ kind: "request", data: request(2) }]);
    queryClient.setQueryData(rangeQueryKey(RUN, "requests"), range([request(1), request(2)], 180));
    expect(hasAppendedRecords(queryClient, RUN)).toBe(false);
  });

  it("イベントで変わらなかったキャッシュは同じオブジェクトのまま", () => {
    const queryClient = seededClient();
    const frames = queryClient.getQueryData(rangeQueryKey(RUN, "frames"));
    writeLiveEvents(queryClient, RUN, [{ kind: "request", data: request(2) }]);
    expect(queryClient.getQueryData(rangeQueryKey(RUN, "frames"))).toBe(frames);
  });
});

describe("writeLiveEvents の advisor_step と読み直し", () => {
  const detail: AdvisorDetail = {
    name: "advisor-tips-001",
    role: "tips",
    request: 1,
    t_start: 1,
    t_end: null,
    status: "running",
    result_json: null,
    steps_available: false,
    steps: [],
    files: [],
  };
  const step = { t: 2, kind: "tool_use", tool: "Read", summary: "notes/tips.md" } as const;

  it("キャッシュにある advisor の実行の詳細に step を足す", () => {
    const queryClient = seededClient();
    queryClient.setQueryData(advisorQueryKey(RUN, detail.name), detail);
    writeLiveEvents(queryClient, RUN, [
      { kind: "advisor_step", data: { ...step, name: detail.name } },
    ]);
    const stored = queryClient.getQueryData<AdvisorDetail>(advisorQueryKey(RUN, detail.name));
    expect(stored?.steps).toEqual([step]);
    expect(stored?.steps_available).toBe(true);
  });

  it("キャッシュに無い advisor の実行の詳細は作らない", () => {
    const queryClient = seededClient();
    writeLiveEvents(queryClient, RUN, [
      { kind: "advisor_step", data: { ...step, name: "advisor-tips-002" } },
    ]);
    expect(queryClient.getQueryData(advisorQueryKey(RUN, "advisor-tips-002"))).toBeUndefined();
  });

  it("読み直すキャッシュ (summary・足す前の最後の要求の詳細) を無効にする", () => {
    const queryClient = seededClient();
    queryClient.setQueryData(requestQueryKey(RUN, 1), { ...request(1) });
    writeLiveEvents(queryClient, RUN, [
      { kind: "run", data: { t: 0, event: "start" } },
      { kind: "request", data: request(2) },
    ]);
    expect(queryClient.getQueryState(summaryQueryKey(RUN))?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(requestQueryKey(RUN, 1))?.isInvalidated).toBe(true);
  });
});
