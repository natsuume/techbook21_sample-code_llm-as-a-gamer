// liveCacheMerge のテスト。届いたイベントをキャッシュに足す規則 (足す位置・置き換え・読み直し・渡した値を変えない) と、
// advisor の実行の詳細の steps に、届いた step を重なりを除いてつなぐ規則を確かめる
import { describe, expect, it } from "vitest";
import type {
  AdvisorStep,
  EventItem,
  FrameItem,
  PadItem,
  RangeResponse,
  RequestItem,
  RoleName,
  RunSummary,
  SummaryAdvisorRun,
  SummaryNoteVersion,
} from "../api/apiTypes.ts";
import { applyLiveEvents, type LiveCaches, mergeAdvisorSteps } from "./liveCacheMerge.ts";

// テスト用の要求。受信時刻は送信時刻の 0.5 秒後
const request = (n: number, tSent: number): RequestItem => ({
  n,
  t_sent: tSent,
  t_received: tSent + 0.5,
  result: "ok",
  action: null,
  advisor_requested: [],
  advisor_accepted: [],
  p: null,
  adopted_count: null,
});

// テスト用のフレーム。判定の出来事の無い rising のフレーム
const frame = (t: number): FrameItem => ({
  t,
  sharpness: 100,
  diff: null,
  phase: "rising",
  peak_t: null,
  peak_sharpness: 100,
  ref: 80,
  event: null,
  thumb: `thumbs/${t}.jpg`,
});

const padRow = (t: number): PadItem => ({ t, source: "apply", state: "A", ok: true, error: null });

const runRow = (t: number, event: string): EventItem => ({ t, event });

// テスト用の区間 API の応答。指定しないキーは、記録があり cursor が 64 の応答にする
const range = <T>(items: T[], fields: Partial<RangeResponse<T>> = {}): RangeResponse<T> => ({
  available: true,
  items,
  cursor: 64,
  ...fields,
});

// テスト用の advisor の実行。指定しないキーは、要求 #1 で呼んで実行中の値にする
const advisorRun = (
  name: string,
  role: RoleName,
  fields: Partial<SummaryAdvisorRun> = {},
): SummaryAdvisorRun => ({
  name,
  role,
  request: 1,
  t_start: 1,
  t_end: null,
  returncode: null,
  denials: null,
  result: null,
  ...fields,
});

// テスト用のゲームの情報の版。指定しないキーは、書き換えの無い版の値にする
const noteVersion = (
  version: number,
  fields: Partial<SummaryNoteVersion> = {},
): SummaryNoteVersion => ({
  version,
  t: version,
  request: version + 1,
  initial: version === 0,
  changed: [],
  ...fields,
});

// テスト用の summary。指定しないキーは、advisor と書き換えの無い実行中の実行の値にする
const summary = (fields: Partial<RunSummary> = {}): RunSummary => ({
  run: "r",
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
  counts: { requests: 0, frames: 0, pad: 0 },
  decide_s_median: null,
  advisor_runs: [],
  note_versions: [],
  recorded: { dashboard: true, requests: true, advisor_steps: true },
  dropped: null,
  skipped_lines: {},
  ...fields,
});

// テスト用のキャッシュ。指定しないものは、記録があり items が空の値にする
const caches = (fields: Partial<LiveCaches> = {}): LiveCaches => ({
  summary: summary(),
  requests: range<RequestItem>([]),
  frames: range<FrameItem>([]),
  pad: range<PadItem>([]),
  events: range<EventItem>([]),
  ...fields,
});

// テスト用の advisor の step
const step = (
  t: number | null,
  tool: string | null,
  kind: AdvisorStep["kind"] = "tool_use",
  text = "notes/tips.md",
): AdvisorStep => ({ t, kind, tool, summary: text });

/** 5 つのキャッシュが、渡したものと同じオブジェクトであることを確かめる */
const expectSameCaches = (actual: LiveCaches, expected: LiveCaches): void => {
  expect(actual.summary).toBe(expected.summary);
  expect(actual.requests).toBe(expected.requests);
  expect(actual.frames).toBe(expected.frames);
  expect(actual.pad).toBe(expected.pad);
  expect(actual.events).toBe(expected.events);
};

describe("applyLiveEvents の区間 API のイベント", () => {
  it("request を届いた順に末尾へ足し、前の要素は同じオブジェクトのまま、cursor は変えない", () => {
    const first = request(1, 1);
    const input = caches({ requests: range([first], { cursor: 640 }) });
    const result = applyLiveEvents(input, [
      { kind: "request", data: request(2, 2) },
      { kind: "request", data: request(3, 3) },
    ]);
    const requests = result.caches.requests;
    expect(requests).not.toBe(input.requests);
    expect(requests?.items).not.toBe(input.requests?.items);
    expect(requests?.items).toEqual([first, request(2, 2), request(3, 3)]);
    expect(requests?.items[0]).toBe(first);
    expect(requests?.cursor).toBe(640);
    expect(requests?.available).toBe(true);
  });

  it("frame・pad・run も届いた順に frames・pad・events の末尾へ足す", () => {
    const input = caches({
      frames: range([frame(1)]),
      pad: range([padRow(1)]),
      events: range([runRow(0, "start")]),
    });
    const result = applyLiveEvents(input, [
      { kind: "frame", data: frame(2) },
      { kind: "pad", data: padRow(2) },
      { kind: "run", data: runRow(2, "capture_stopped") },
      { kind: "frame", data: frame(3) },
    ]);
    expect(result.caches.frames?.items).toEqual([frame(1), frame(2), frame(3)]);
    expect(result.caches.frames?.items[0]).toBe(input.frames?.items[0]);
    expect(result.caches.pad?.items).toEqual([padRow(1), padRow(2)]);
    expect(result.caches.events?.items).toEqual([runRow(0, "start"), runRow(2, "capture_stopped")]);
    expect(result.caches.frames?.cursor).toBe(64);
    expect(result.invalidations).toEqual([]);
  });

  it("足さなかった種類のキャッシュと summary は同じオブジェクトのまま", () => {
    const input = caches({ requests: range([request(1, 1)]) });
    const result = applyLiveEvents(input, [{ kind: "frame", data: frame(1) }]);
    expect(result.caches.frames).not.toBe(input.frames);
    expect(result.caches.summary).toBe(input.summary);
    expect(result.caches.requests).toBe(input.requests);
    expect(result.caches.pad).toBe(input.pad);
    expect(result.caches.events).toBe(input.events);
  });

  it("イベントが無ければ、キャッシュはすべて同じオブジェクトで、読み直しも step も end も無い", () => {
    const input = caches();
    const result = applyLiveEvents(input, []);
    expectSameCaches(result.caches, input);
    expect(result.invalidations).toEqual([]);
    expect(result.advisorSteps.size).toBe(0);
    expect(result.end).toBeNull();
  });

  it("available が false の種類に足すと、true にして reason を除き、summary を読み直す", () => {
    const input = caches({
      frames: range<FrameItem>([], { available: false, reason: "old_format", cursor: 0 }),
    });
    const result = applyLiveEvents(input, [{ kind: "frame", data: frame(1) }]);
    expect(result.caches.frames).toStrictEqual({ available: true, items: [frame(1)], cursor: 0 });
    expect(result.invalidations).toEqual([{ type: "summary" }]);
  });

  it("event が start の run が届いたら summary を読み直す", () => {
    const result = applyLiveEvents(caches(), [{ kind: "run", data: runRow(0, "start") }]);
    expect(result.caches.events?.items).toEqual([runRow(0, "start")]);
    expect(result.invalidations).toEqual([{ type: "summary" }]);
  });

  it("request が届くと、足す前の最後の要求の詳細を読み直す (届いた要求は読み直さない)", () => {
    const input = caches({ requests: range([request(4, 1), request(5, 2)]) });
    const result = applyLiveEvents(input, [
      { kind: "request", data: request(6, 3) },
      { kind: "request", data: request(7, 4) },
    ]);
    expect(result.invalidations).toEqual([{ type: "request", n: 5 }]);
  });

  it("足す前の要求が無ければ、要求の詳細を読み直さない", () => {
    const result = applyLiveEvents(caches(), [{ kind: "request", data: request(1, 1) }]);
    expect(result.caches.requests?.items).toEqual([request(1, 1)]);
    expect(result.invalidations).toEqual([]);
  });
});

describe("applyLiveEvents の note_version", () => {
  it("同じ番号の版を置き換え、値が変わったらその版を読み直す", () => {
    const input = caches({
      summary: summary({
        note_versions: [
          noteVersion(0),
          noteVersion(1, { changed: [{ kind: "tips", by: "external" }] }),
        ],
      }),
    });
    const changed = noteVersion(1, { changed: [{ kind: "tips", by: "advisor-tips-001" }] });
    const result = applyLiveEvents(input, [{ kind: "note_version", data: changed }]);
    expect(result.caches.summary?.note_versions).toEqual([noteVersion(0), changed]);
    expect(result.invalidations).toEqual([{ type: "note", version: 1 }]);
  });

  it("同じ値の版なら読み直さない", () => {
    const input = caches({ summary: summary({ note_versions: [noteVersion(0), noteVersion(1)] }) });
    const result = applyLiveEvents(input, [{ kind: "note_version", data: noteVersion(1) }]);
    expect(result.caches.summary?.note_versions).toEqual([noteVersion(0), noteVersion(1)]);
    expect(result.invalidations).toEqual([]);
  });

  it("新しい版は番号の昇順の位置に挿し、読み直さない", () => {
    const input = caches({ summary: summary({ note_versions: [noteVersion(0), noteVersion(2)] }) });
    const result = applyLiveEvents(input, [
      { kind: "note_version", data: noteVersion(3) },
      { kind: "note_version", data: noteVersion(1) },
    ]);
    expect(result.caches.summary?.note_versions).toEqual([
      noteVersion(0),
      noteVersion(1),
      noteVersion(2),
      noteVersion(3),
    ]);
    expect(result.invalidations).toEqual([]);
  });
});

describe("applyLiveEvents の advisor_run", () => {
  const names = (runs: SummaryAdvisorRun[] | undefined): string[] | undefined =>
    runs?.map((run) => run.name);

  it("同じ名前の実行を置き換え、値が変わったらその実行の詳細を読み直す", () => {
    const controls = advisorRun("advisor-controls-001", "controls");
    const done = advisorRun("advisor-strategy-001", "strategy", {
      t_end: 9,
      returncode: 0,
      denials: 1,
      result: "ok",
    });
    const input = caches({
      summary: summary({
        advisor_runs: [controls, advisorRun("advisor-strategy-001", "strategy")],
      }),
    });
    const result = applyLiveEvents(input, [{ kind: "advisor_run", data: done }]);
    expect(result.caches.summary?.advisor_runs).toEqual([controls, done]);
    expect(result.invalidations).toEqual([{ type: "advisor", name: "advisor-strategy-001" }]);
  });

  it("同じ値の実行なら読み直さない", () => {
    const input = caches({
      summary: summary({ advisor_runs: [advisorRun("advisor-tips-001", "tips")] }),
    });
    const result = applyLiveEvents(input, [
      { kind: "advisor_run", data: advisorRun("advisor-tips-001", "tips") },
    ]);
    expect(result.caches.summary?.advisor_runs).toEqual([advisorRun("advisor-tips-001", "tips")]);
    expect(result.invalidations).toEqual([]);
  });

  it("新しい実行は役割の順、同じ役割の中は名前の末尾の連番の昇順の位置に挿し、読み直さない", () => {
    const input = caches({
      summary: summary({
        advisor_runs: [
          advisorRun("advisor-controls-001", "controls"),
          advisorRun("advisor-strategy-009", "strategy"),
          advisorRun("advisor-tips-001", "tips"),
        ],
      }),
    });
    const result = applyLiveEvents(input, [
      { kind: "advisor_run", data: advisorRun("advisor-strategy-010", "strategy") },
      { kind: "advisor_run", data: advisorRun("advisor-advice-001", "advice") },
      { kind: "advisor_run", data: advisorRun("advisor-controls-002", "controls") },
    ]);
    expect(names(result.caches.summary?.advisor_runs)).toEqual([
      "advisor-controls-001",
      "advisor-controls-002",
      "advisor-strategy-009",
      "advisor-strategy-010",
      "advisor-advice-001",
      "advisor-tips-001",
    ]);
    expect(result.invalidations).toEqual([]);
  });

  it("連番は文字列ではなく数として比べる", () => {
    const before999 = caches({
      summary: summary({ advisor_runs: [advisorRun("advisor-strategy-999", "strategy")] }),
    });
    expect(
      names(
        applyLiveEvents(before999, [
          { kind: "advisor_run", data: advisorRun("advisor-strategy-1000", "strategy") },
        ]).caches.summary?.advisor_runs,
      ),
    ).toEqual(["advisor-strategy-999", "advisor-strategy-1000"]);

    const before1000 = caches({
      summary: summary({ advisor_runs: [advisorRun("advisor-strategy-1000", "strategy")] }),
    });
    expect(
      names(
        applyLiveEvents(before1000, [
          { kind: "advisor_run", data: advisorRun("advisor-strategy-999", "strategy") },
        ]).caches.summary?.advisor_runs,
      ),
    ).toEqual(["advisor-strategy-999", "advisor-strategy-1000"]);
  });

  it("連番を読めない名前は、その役割の最後に挿す", () => {
    const input = caches({
      summary: summary({
        advisor_runs: [
          advisorRun("advisor-strategy-001", "strategy"),
          advisorRun("advisor-strategy-002", "strategy"),
          advisorRun("advisor-advice-001", "advice"),
        ],
      }),
    });
    const result = applyLiveEvents(input, [
      { kind: "advisor_run", data: advisorRun("advisor-strategy-extra", "strategy") },
    ]);
    expect(names(result.caches.summary?.advisor_runs)).toEqual([
      "advisor-strategy-001",
      "advisor-strategy-002",
      "advisor-strategy-extra",
      "advisor-advice-001",
    ]);
  });
});

describe("applyLiveEvents の stats・advisor_step・end", () => {
  it("stats は t_end・counts・decide_s_median だけを置き換え、複数なら最後の値にする", () => {
    const before = summary({
      t_end: 10,
      counts: { requests: 1, frames: 2, pad: 3 },
      decide_s_median: 0.5,
      advisor_runs: [advisorRun("advisor-tips-001", "tips")],
    });
    const input = caches({ summary: before });
    const result = applyLiveEvents(input, [
      {
        kind: "stats",
        data: { t_end: 11, counts: { requests: 2, frames: 5, pad: 3 }, decide_s_median: 0.4 },
      },
      {
        kind: "stats",
        data: { t_end: 12.5, counts: { requests: 3, frames: 9, pad: 4 }, decide_s_median: null },
      },
    ]);
    expect(result.caches.summary).toEqual({
      ...before,
      t_end: 12.5,
      counts: { requests: 3, frames: 9, pad: 4 },
      decide_s_median: null,
    });
    expect(result.caches.summary?.advisor_runs).toBe(before.advisor_runs);
    expect(result.invalidations).toEqual([]);
  });

  it("advisor_step はキャッシュを変えず、実行の名前ごとに届いた順で name を除いて返す", () => {
    const input = caches();
    const result = applyLiveEvents(input, [
      { kind: "advisor_step", data: { ...step(1, "Read"), name: "advisor-tips-001" } },
      { kind: "advisor_step", data: { ...step(2, "Write"), name: "advisor-strategy-001" } },
      {
        kind: "advisor_step",
        data: { ...step(3, null, "result", "done"), name: "advisor-tips-001" },
      },
    ]);
    expect(result.advisorSteps.size).toBe(2);
    expect(result.advisorSteps.get("advisor-tips-001")).toStrictEqual([
      step(1, "Read"),
      step(3, null, "result", "done"),
    ]);
    expect(result.advisorSteps.get("advisor-strategy-001")).toStrictEqual([step(2, "Write")]);
    expectSameCaches(result.caches, input);
    expect(result.invalidations).toEqual([]);
  });

  it("end の data を返し (複数なら最後)、キャッシュは変えない", () => {
    const input = caches();
    const result = applyLiveEvents(input, [
      { kind: "end", data: { record_end: false } },
      { kind: "end", data: { record_end: true } },
    ]);
    expect(result.end).toEqual({ record_end: true });
    expectSameCaches(result.caches, input);
  });
});

describe("applyLiveEvents の読めていないキャッシュと読み直しの重なり", () => {
  it("キャッシュが undefined の種類のイベントは、例外を出さずに捨てる", () => {
    const input: LiveCaches = {
      summary: summary(),
      requests: undefined,
      frames: undefined,
      pad: undefined,
      events: undefined,
    };
    const result = applyLiveEvents(input, [
      { kind: "request", data: request(1, 1) },
      { kind: "frame", data: frame(1) },
      { kind: "pad", data: padRow(1) },
      { kind: "run", data: runRow(1, "capture_stopped") },
    ]);
    expectSameCaches(result.caches, input);
    expect(result.caches.requests).toBeUndefined();
    expect(result.invalidations).toEqual([]);
  });

  it("summary が undefined なら note_version・advisor_run・stats は、例外を出さずに捨てる", () => {
    const input = caches({ summary: undefined });
    const result = applyLiveEvents(input, [
      { kind: "note_version", data: noteVersion(0) },
      { kind: "advisor_run", data: advisorRun("advisor-tips-001", "tips") },
      {
        kind: "stats",
        data: { t_end: 1, counts: { requests: 1, frames: 1, pad: 1 }, decide_s_median: null },
      },
    ]);
    expect(result.caches.summary).toBeUndefined();
    expectSameCaches(result.caches, input);
    expect(result.invalidations).toEqual([]);
  });

  it("同じ読み直しは 1 回だけ、最初に出た順に返す", () => {
    const input = caches({
      summary: summary({
        note_versions: [noteVersion(0), noteVersion(1)],
        advisor_runs: [advisorRun("advisor-tips-001", "tips")],
      }),
      frames: range<FrameItem>([], { available: false }),
    });
    const result = applyLiveEvents(input, [
      { kind: "run", data: runRow(0, "start") },
      { kind: "note_version", data: noteVersion(1, { t: 5 }) },
      { kind: "frame", data: frame(1) },
      { kind: "advisor_run", data: advisorRun("advisor-tips-001", "tips", { t_end: 4 }) },
      { kind: "note_version", data: noteVersion(1, { t: 6 }) },
    ]);
    expect(result.invalidations).toEqual([
      { type: "summary" },
      { type: "note", version: 1 },
      { type: "advisor", name: "advisor-tips-001" },
    ]);
  });

  it("渡したキャッシュを変えない", () => {
    const input = caches({
      summary: summary({
        note_versions: [noteVersion(0), noteVersion(1)],
        advisor_runs: [advisorRun("advisor-tips-001", "tips")],
      }),
      requests: range([request(1, 1)]),
      frames: range<FrameItem>([], { available: false, reason: "old_format" }),
    });
    const snapshot = structuredClone(input);
    applyLiveEvents(input, [
      { kind: "request", data: request(2, 2) },
      { kind: "frame", data: frame(1) },
      { kind: "pad", data: padRow(1) },
      { kind: "run", data: runRow(1, "start") },
      { kind: "note_version", data: noteVersion(1, { t: 5 }) },
      { kind: "note_version", data: noteVersion(2) },
      { kind: "advisor_run", data: advisorRun("advisor-tips-001", "tips", { t_end: 4 }) },
      { kind: "advisor_run", data: advisorRun("advisor-controls-001", "controls") },
      {
        kind: "stats",
        data: { t_end: 3, counts: { requests: 2, frames: 1, pad: 1 }, decide_s_median: 0.2 },
      },
      { kind: "advisor_step", data: { ...step(1, "Read"), name: "advisor-tips-001" } },
      { kind: "end", data: { record_end: true } },
    ]);
    expect(input).toStrictEqual(snapshot);
  });
});

describe("mergeAdvisorSteps", () => {
  const a = step(1, "Read");
  const b = step(2, "Grep");
  const c = step(3, "Write");
  const d = step(4, "Bash");

  it("重なりが無ければ、詳細の steps の後に届いた step をすべて続ける", () => {
    expect(mergeAdvisorSteps([a, b], [c, d])).toEqual([a, b, c, d]);
  });

  it("詳細の末尾と届いた step の先頭が重なれば、重なった分を 1 回だけにする", () => {
    expect(mergeAdvisorSteps([a, b, c], [b, c, d])).toEqual([a, b, c, d]);
  });

  it("届いた step がすべて詳細の末尾に含まれていれば、詳細の steps のまま", () => {
    expect(mergeAdvisorSteps([a, b, c], [b, c])).toEqual([a, b, c]);
  });

  it("詳細の steps が空なら、届いた step のまま", () => {
    expect(mergeAdvisorSteps([], [a, b])).toEqual([a, b]);
  });

  it("届いた step が空なら、詳細の steps のまま", () => {
    expect(mergeAdvisorSteps([a, b], [])).toEqual([a, b]);
  });

  it("届いた step の中の同じ値の step は、まとめずに両方残す", () => {
    const readAgain = step(5, "Read", "tool_use", "notes/tips.md");
    expect(mergeAdvisorSteps([a], [readAgain, readAgain])).toEqual([a, readAgain, readAgain]);
  });

  it("重なった step は詳細のものを使う (kind は一致の判定に使わない)", () => {
    const denied = step(1, "Read", "permission_denied");
    const streamedResult = step(1, "Read", "tool_result");
    const merged = mergeAdvisorSteps([denied], [streamedResult, b]);
    expect(merged).toEqual([denied, b]);
    expect(merged[0]).toBe(denied);
  });

  it("詳細が [A] で届いた step が [A, A] なら、重なりは 1 件で [A, A]", () => {
    expect(mergeAdvisorSteps([a], [a, a])).toEqual([a, a]);
  });

  it("重なりは、一致する最も長い末尾と先頭にする", () => {
    expect(mergeAdvisorSteps([a, a], [a, a, b])).toEqual([a, a, b]);
  });

  describe("詳細が届いた step より先に進んでいるとき", () => {
    const steps = [0, 1, 2, 3, 4, 5].map((t) => step(t, "Read", "tool_use", `file-${t}.md`));
    const [s0, s1, s2, s3, s4, s5] = steps as [
      AdvisorStep,
      AdvisorStep,
      AdvisorStep,
      AdvisorStep,
      AdvisorStep,
      AdvisorStep,
    ];

    it("届いた step がすべて詳細の先頭からに含まれていれば、詳細の steps のまま", () => {
      expect(mergeAdvisorSteps([s1, s2, s3, s4, s5], [s1, s2, s3])).toEqual([s1, s2, s3, s4, s5]);
    });

    it("届いた step がすべて詳細の途中からに含まれていれば、詳細の steps のまま", () => {
      expect(mergeAdvisorSteps([s0, s1, s2, s3, s4, s5], [s2, s3, s4])).toEqual([
        s0,
        s1,
        s2,
        s3,
        s4,
        s5,
      ]);
    });

    it("届いた step が詳細の途中から始まって詳細より先まであれば、詳細の後に足りない分だけを続ける", () => {
      expect(mergeAdvisorSteps([s0, s1, s2, s3], [s2, s3, s4, s5])).toEqual([
        s0,
        s1,
        s2,
        s3,
        s4,
        s5,
      ]);
    });

    it("同じ値の step は、詳細の中の数を保つ", () => {
      expect(mergeAdvisorSteps([a, a], [a])).toEqual([a, a]);
    });
  });

  it("渡した配列は変えない", () => {
    const fetched = [a, b];
    const streamed = [b, c];
    mergeAdvisorSteps(fetched, streamed);
    expect(fetched).toEqual([a, b]);
    expect(streamed).toEqual([b, c]);
  });
});
