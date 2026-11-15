// liveQueryCache のテスト。本物の QueryClient のキャッシュに、届いたイベントを足すこと・イベントで足した記録の有無・
// つなぐ位置・advisor の途中経過の置き場への足し方と空にする・捨てる動き・読み直すキャッシュの無効化を確かめる
import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import type {
  AdvisorDetail,
  AdvisorStep,
  EventItem,
  FrameItem,
  PadItem,
  RangeResponse,
  RequestItem,
  RunSummary,
  SummaryAdvisorRun,
  SummaryNoteVersion,
} from "../api/apiTypes.ts";
import {
  advisorQueryKey,
  advisorQueryKeyPrefix,
  createQueryClient,
  rangeQueryKey,
  requestQueryKey,
  streamedStepsQueryKey,
  summaryQueryKey,
} from "../api/queries.ts";
import {
  clearStreamedSteps,
  clearSummaryOverrides,
  createSummaryOverridesHolder,
  discardStreamedSteps,
  hasAppendedRecords,
  hasStaleRecords,
  invalidateAfterEnd,
  liveCursorsOf,
  markRunRecordsStale,
  refetchRunRecords,
  writeLiveEvents,
} from "./liveQueryCache.ts";

const RUN = "20260101-120000";
const OTHER_RUN = "20260102-090000";

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

describe("markRunRecordsStale と hasStaleRecords", () => {
  it("サーバーから読んだだけのキャッシュには、古いものとしての印が無い", () => {
    expect(hasStaleRecords(seededClient(), RUN)).toBe(false);
  });

  it("印を付けると、summary と 4 つの区間 API が古いものになり、値はそのまま残る", () => {
    const queryClient = seededClient();
    markRunRecordsStale(queryClient, RUN);
    expect(hasStaleRecords(queryClient, RUN)).toBe(true);
    for (const queryKey of [
      summaryQueryKey(RUN),
      rangeQueryKey(RUN, "requests"),
      rangeQueryKey(RUN, "frames"),
      rangeQueryKey(RUN, "pad"),
      rangeQueryKey(RUN, "events"),
    ]) {
      expect(queryClient.getQueryState(queryKey)?.isInvalidated).toBe(true);
    }
    expect(
      queryClient.getQueryData<RangeResponse<RequestItem>>(rangeQueryKey(RUN, "requests"))?.items,
    ).toEqual([request(1)]);
  });

  it("ほかの実行のキャッシュには印を付けない", () => {
    const queryClient = seededClient();
    markRunRecordsStale(queryClient, "20261231-000000");
    expect(hasStaleRecords(queryClient, RUN)).toBe(false);
  });
});

describe("writeLiveEvents の advisor_step と、途中経過の置き場", () => {
  const NAME = "advisor-tips-001";
  const detail: AdvisorDetail = {
    name: NAME,
    role: "tips",
    request: 1,
    t_start: 1,
    t_end: null,
    status: "running",
    result_json: null,
    steps_available: true,
    steps: [{ t: 1, kind: "tool_use", tool: "Glob", summary: "notes/*" }],
    files: [],
  };
  const step = (t: number, summary = "notes/tips.md"): AdvisorStep => ({
    t,
    kind: "tool_use",
    tool: "Read",
    summary,
  });
  const streamedOf = (queryClient: QueryClient, name: string, run = RUN) =>
    queryClient.getQueryData<AdvisorStep[]>(streamedStepsQueryKey(run, name));

  it("届いた step を、詳細のキャッシュが無くても、実行ごとの置き場に届いた順に足す", () => {
    const queryClient = seededClient();
    writeLiveEvents(queryClient, RUN, [
      { kind: "advisor_step", data: { ...step(2), name: NAME } },
      { kind: "advisor_step", data: { ...step(3), name: "advisor-strategy-001" } },
    ]);
    writeLiveEvents(queryClient, RUN, [
      { kind: "advisor_step", data: { ...step(4), name: NAME } },
      { kind: "advisor_step", data: { ...step(4), name: NAME } },
    ]);
    expect(streamedOf(queryClient, NAME)).toEqual([step(2), step(4), step(4)]);
    expect(streamedOf(queryClient, "advisor-strategy-001")).toEqual([step(3)]);
    expect(queryClient.getQueryData(advisorQueryKey(RUN, NAME))).toBeUndefined();
  });

  it("詳細のキャッシュは変えず、詳細の無効化と読み直し (書き換え) でも置き場は変わらない", () => {
    const queryClient = seededClient();
    queryClient.setQueryData(advisorQueryKey(RUN, NAME), detail);
    writeLiveEvents(queryClient, RUN, [{ kind: "advisor_step", data: { ...step(2), name: NAME } }]);
    expect(queryClient.getQueryData(advisorQueryKey(RUN, NAME))).toEqual(detail);
    void queryClient.invalidateQueries({ queryKey: advisorQueryKeyPrefix(RUN) });
    queryClient.setQueryData(advisorQueryKey(RUN, NAME), { ...detail, steps: [] });
    expect(streamedOf(queryClient, NAME)).toEqual([step(2)]);
  });

  it("clearStreamedSteps は、その実行の置き場をすべて空にし、ほかの実行の置き場は残す", () => {
    const queryClient = seededClient();
    writeLiveEvents(queryClient, RUN, [
      { kind: "advisor_step", data: { ...step(2), name: NAME } },
      { kind: "advisor_step", data: { ...step(3), name: "advisor-strategy-001" } },
    ]);
    writeLiveEvents(queryClient, OTHER_RUN, [
      { kind: "advisor_step", data: { ...step(5), name: NAME } },
    ]);
    clearStreamedSteps(queryClient, RUN);
    expect(streamedOf(queryClient, NAME)).toEqual([]);
    expect(streamedOf(queryClient, "advisor-strategy-001")).toEqual([]);
    expect(streamedOf(queryClient, NAME, OTHER_RUN)).toEqual([step(5)]);
    writeLiveEvents(queryClient, RUN, [{ kind: "advisor_step", data: { ...step(6), name: NAME } }]);
    expect(streamedOf(queryClient, NAME)).toEqual([step(6)]);
  });

  it("discardStreamedSteps は、その実行の置き場を捨て、ほかの実行の置き場は残す", () => {
    const queryClient = seededClient();
    writeLiveEvents(queryClient, RUN, [{ kind: "advisor_step", data: { ...step(2), name: NAME } }]);
    writeLiveEvents(queryClient, OTHER_RUN, [
      { kind: "advisor_step", data: { ...step(5), name: NAME } },
    ]);
    discardStreamedSteps(queryClient, RUN);
    expect(streamedOf(queryClient, NAME)).toBeUndefined();
    expect(streamedOf(queryClient, NAME, OTHER_RUN)).toEqual([step(5)]);
  });

  it("画面の QueryClient では、置き場を使う画面が無い間も捨てない (gcTime が Infinity)", () => {
    const queryClient = createQueryClient();
    writeLiveEvents(queryClient, RUN, [{ kind: "advisor_step", data: { ...step(2), name: NAME } }]);
    const query = queryClient.getQueryCache().find({ queryKey: streamedStepsQueryKey(RUN, NAME) });
    expect(query?.options.gcTime).toBe(Number.POSITIVE_INFINITY);
  });
});

describe("refetchRunRecords", () => {
  /** summary と 4 つの区間 API を、queryFn 付きで読み込んだ QueryClient (failing の queryKey の読み直しは失敗する) */
  const loadedClient = async (failing: { key: readonly unknown[] | null }) => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const keys = [
      summaryQueryKey(RUN),
      rangeQueryKey(RUN, "requests"),
      rangeQueryKey(RUN, "frames"),
      rangeQueryKey(RUN, "pad"),
      rangeQueryKey(RUN, "events"),
    ];
    for (const key of keys) {
      await queryClient.prefetchQuery({
        queryKey: key,
        queryFn: async () => {
          if (failing.key !== null && JSON.stringify(failing.key) === JSON.stringify(key)) {
            throw new Error("読めない");
          }
          return key[0] === "summary" ? summary : range<RequestItem>([], 0);
        },
      });
    }
    return queryClient;
  };

  it("summary と 4 つの区間 API をすべて読み直せたら true", async () => {
    const queryClient = await loadedClient({ key: null });
    expect(await refetchRunRecords(queryClient, RUN)).toBe(true);
  });

  it("どれかが読めなければ false (開き直さない判断に使う)", async () => {
    const failing: { key: readonly unknown[] | null } = { key: null };
    const queryClient = await loadedClient(failing);
    failing.key = rangeQueryKey(RUN, "frames");
    expect(await refetchRunRecords(queryClient, RUN)).toBe(false);
    failing.key = summaryQueryKey(RUN);
    expect(await refetchRunRecords(queryClient, RUN)).toBe(false);
  });

  it("その実行のすべての要求の詳細を無効にし、ほかの実行の要求の詳細は変えない", async () => {
    const queryClient = await loadedClient({ key: null });
    queryClient.setQueryData(requestQueryKey(RUN, 1), { ...request(1) });
    queryClient.setQueryData(requestQueryKey(RUN, 2), { ...request(2) });
    queryClient.setQueryData(requestQueryKey(OTHER_RUN, 1), { ...request(1) });
    await refetchRunRecords(queryClient, RUN);
    expect(queryClient.getQueryState(requestQueryKey(RUN, 1))?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(requestQueryKey(RUN, 2))?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(requestQueryKey(OTHER_RUN, 1))?.isInvalidated).toBe(false);
  });
});

describe("invalidateAfterEnd", () => {
  it("その実行の summary とすべての要求の詳細を無効にし、ほかの実行と区間 API は変えない", () => {
    const queryClient = seededClient();
    queryClient.setQueryData(requestQueryKey(RUN, 1), { ...request(1) });
    queryClient.setQueryData(requestQueryKey(RUN, 2), { ...request(2) });
    queryClient.setQueryData(requestQueryKey(OTHER_RUN, 1), { ...request(1) });
    invalidateAfterEnd(queryClient, RUN);
    expect(queryClient.getQueryState(summaryQueryKey(RUN))?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(requestQueryKey(RUN, 1))?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(requestQueryKey(RUN, 2))?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(requestQueryKey(OTHER_RUN, 1))?.isInvalidated).toBe(false);
    expect(queryClient.getQueryState(rangeQueryKey(RUN, "requests"))?.isInvalidated).toBe(false);
  });
});

describe("writeLiveEvents の読み直し", () => {
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

describe("上書き用の値の入れ物と、summary の読み直しの後の当て直し", () => {
  const newVersion: SummaryNoteVersion = {
    version: 1,
    t: 3,
    request: 1,
    initial: false,
    changed: [{ kind: "tips", by: "advisor-tips-001" }],
  };
  const newRun: SummaryAdvisorRun = {
    name: "advisor-tips-001",
    role: "tips",
    request: 1,
    t_start: 1,
    t_end: null,
    returncode: null,
    denials: null,
    result: null,
  };
  const newStats = {
    t_end: 4,
    counts: { requests: 1, frames: 5, pad: 2 },
    decide_s_median: 0.25,
  };
  const oldSummary: RunSummary = { ...summary, note_versions: [], advisor_runs: [] };

  /**
   * summary を使っている (observer のある) QueryClient を作る。最初の読み込みは oldSummary を返し、その後の読み直しは
   * respond を呼ぶまで応答を遅らせる (呼ぶと、その値か例外で応答する)
   */
  const observedClient = async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let calls = 0;
    const pending: ((outcome: RunSummary | Error) => void)[] = [];
    const observer = new QueryObserver<RunSummary>(queryClient, {
      queryKey: summaryQueryKey(RUN),
      queryFn: () => {
        calls += 1;
        if (calls === 1) {
          return Promise.resolve(oldSummary);
        }
        return new Promise<RunSummary>((resolve, reject) => {
          pending.push((outcome) =>
            outcome instanceof Error ? reject(outcome) : resolve(outcome),
          );
        });
      },
      staleTime: Number.POSITIVE_INFINITY,
    });
    const unsubscribe = observer.subscribe(() => {});
    await vi.waitFor(() => expect(queryClient.getQueryData(summaryQueryKey(RUN))).toBeDefined());
    queryClient.setQueryData(rangeQueryKey(RUN, "events"), range<EventItem>([], 400));
    return {
      queryClient,
      unsubscribe,
      respond: (outcome: RunSummary | Error) => pending.shift()?.(outcome),
      pendingCount: () => pending.length,
    };
  };
  const summaryOf = (queryClient: QueryClient) =>
    queryClient.getQueryData<RunSummary>(summaryQueryKey(RUN));

  it("summary の読み直しの応答が遅れる間に足した note_version・advisor_run・stats が、応答の後も残る", async () => {
    const { queryClient, unsubscribe, respond, pendingCount } = await observedClient();
    const holder = createSummaryOverridesHolder();
    // start の行は summary の読み直しを起こす
    writeLiveEvents(queryClient, RUN, [{ kind: "run", data: { t: 0, event: "start" } }], holder);
    await vi.waitFor(() => expect(pendingCount()).toBe(1));
    writeLiveEvents(
      queryClient,
      RUN,
      [
        { kind: "note_version", data: newVersion },
        { kind: "advisor_run", data: newRun },
        { kind: "stats", data: newStats },
      ],
      holder,
    );
    expect(summaryOf(queryClient)?.note_versions).toEqual([newVersion]);
    // 読み直しの応答は、足した値を含まない古い summary
    respond(oldSummary);
    await vi.waitFor(() =>
      expect(queryClient.getQueryState(summaryQueryKey(RUN))?.fetchStatus).toBe("idle"),
    );
    await vi.waitFor(() => expect(summaryOf(queryClient)?.note_versions).toEqual([newVersion]));
    const after = summaryOf(queryClient);
    expect(after?.advisor_runs).toEqual([newRun]);
    expect(after?.t_end).toBe(newStats.t_end);
    expect(after?.counts).toEqual(newStats.counts);
    expect(after?.decide_s_median).toBe(newStats.decide_s_median);
    unsubscribe();
  });

  it("読み直しに失敗したときは、キャッシュの前の値 (足した値を含む) がそのまま残る", async () => {
    const { queryClient, unsubscribe, respond, pendingCount } = await observedClient();
    const holder = createSummaryOverridesHolder();
    writeLiveEvents(queryClient, RUN, [{ kind: "run", data: { t: 0, event: "start" } }], holder);
    await vi.waitFor(() => expect(pendingCount()).toBe(1));
    writeLiveEvents(queryClient, RUN, [{ kind: "note_version", data: newVersion }], holder);
    respond(new Error("読めない"));
    await vi.waitFor(() =>
      expect(queryClient.getQueryState(summaryQueryKey(RUN))?.status).toBe("error"),
    );
    expect(summaryOf(queryClient)?.note_versions).toEqual([newVersion]);
    unsubscribe();
  });

  it("入れ物は summary 向けのイベントの最新の値を持ち、clearSummaryOverrides で空になる", () => {
    const holder = createSummaryOverridesHolder();
    writeLiveEvents(
      seededClient(),
      RUN,
      [
        { kind: "note_version", data: newVersion },
        { kind: "advisor_run", data: newRun },
        { kind: "stats", data: newStats },
      ],
      holder,
    );
    expect([...holder.current.noteVersions.values()]).toEqual([newVersion]);
    expect([...holder.current.advisorRuns.values()]).toEqual([newRun]);
    expect(holder.current.stats).toEqual(newStats);
    clearSummaryOverrides(holder);
    expect(holder.current.noteVersions.size).toBe(0);
    expect(holder.current.advisorRuns.size).toBe(0);
    expect(holder.current.stats).toBeNull();
  });
});
