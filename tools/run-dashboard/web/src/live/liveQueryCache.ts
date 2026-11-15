// ライブで使う、TanStack Query のキャッシュの読み書き。届いたイベントを summary・区間 API のキャッシュと、advisor の
// 途中経過の置き場に足し、読み直すキャッシュを無効にする (足し込みが起こした summary の読み直しの後には、受け取った
// summary 向けの値を当て直す)。/live につなぐ位置 (cursor) を区間 API のキャッシュから読み、区間 API と summary を
// 読み直す。
import type { QueryClient } from "@tanstack/react-query";
import type { RangeKind } from "../api/apiPaths.ts";
import type {
  AdvisorStep,
  LiveCursors,
  LiveEvent,
  RangeResponse,
  RunSummary,
} from "../api/apiTypes.ts";
import {
  advisorQueryKey,
  noteQueryKey,
  RANGE_KINDS,
  type RangeItemOf,
  rangeQueryKey,
  requestQueryKey,
  requestQueryKeyPrefix,
  streamedStepsQueryKey,
  streamedStepsQueryKeyPrefix,
  summaryQueryKey,
} from "../api/queries.ts";
import {
  applyLiveEvents,
  applySummaryOverrides,
  EMPTY_SUMMARY_OVERRIDES,
  type LiveCaches,
  type LiveInvalidation,
  type LiveMergeResult,
  recordSummaryOverrides,
  type SummaryOverrides,
} from "./liveCacheMerge.ts";

/**
 * イベントを足して作った区間 API の応答 (キャッシュに保存された値)。cursor はサーバーから読んだときの値のままで、
 * 足した記録より前を指すため、この応答のままつなぐと同じ記録が二重に届く。つなぐ前に区間 API を読み直すかの判定に
 * 使う (オブジェクトの同一性で見分ける。読み直すとサーバーの応答から作った別のオブジェクトに替わる)
 */
const appendedRanges = new WeakSet<object>();

const getRange = <K extends RangeKind>(
  queryClient: QueryClient,
  run: string,
  kind: K,
): RangeResponse<RangeItemOf[K]> | undefined =>
  queryClient.getQueryData<RangeResponse<RangeItemOf[K]>>(rangeQueryKey(run, kind));

/** 実行 run の summary と 4 つの区間 API のキャッシュの値 (無ければ undefined) を返す */
const readLiveCaches = (queryClient: QueryClient, run: string): LiveCaches => ({
  summary: queryClient.getQueryData<RunSummary>(summaryQueryKey(run)),
  requests: getRange(queryClient, run, "requests"),
  frames: getRange(queryClient, run, "frames"),
  pad: getRange(queryClient, run, "pad"),
  events: getRange(queryClient, run, "events"),
});

/**
 * 区間 API のキャッシュを、変わっていれば after にし、保存された値を appendedRanges に入れる (setQueryData は前の値と
 * 構造を共有した別のオブジェクトを保存するため、渡した after ではなく戻り値を入れる)
 */
const writeRange = <K extends RangeKind>(
  queryClient: QueryClient,
  run: string,
  kind: K,
  before: RangeResponse<RangeItemOf[K]> | undefined,
  after: RangeResponse<RangeItemOf[K]> | undefined,
): void => {
  if (after === undefined || after === before) {
    return;
  }
  const stored = queryClient.setQueryData<RangeResponse<RangeItemOf[K]>>(
    rangeQueryKey(run, kind),
    after,
  );
  if (stored !== undefined) {
    appendedRanges.add(stored);
  }
};

/** 読み直すキャッシュの queryKey */
const invalidationQueryKey = (run: string, invalidation: LiveInvalidation) => {
  switch (invalidation.type) {
    case "summary":
      return summaryQueryKey(run);
    case "request":
      return requestQueryKey(run, invalidation.n);
    case "advisor":
      return advisorQueryKey(run, invalidation.name);
    case "note":
      return noteQueryKey(run, invalidation.version);
  }
};

/**
 * 1 つの接続の上書き用の値 (SummaryOverrides) の入れ物。writeLiveEvents が足し、ライブが起こした summary の読み直しの
 * 後に、その時点の値を当て直す。接続を開くたびに clearSummaryOverrides で空にする
 */
export type SummaryOverridesHolder = { current: SummaryOverrides };

/** 空の上書き用の値の入れ物を作る */
export const createSummaryOverridesHolder = (): SummaryOverridesHolder => ({
  current: EMPTY_SUMMARY_OVERRIDES,
});

/** 上書き用の値の入れ物を空にする (サーバーは接続した直後に advisor_run・note_version のすべてと stats を送るため) */
export const clearSummaryOverrides = (holder: SummaryOverridesHolder): void => {
  holder.current = EMPTY_SUMMARY_OVERRIDES;
};

/**
 * 実行 run の summary のキャッシュに、入れ物の今の上書き用の値を当て直す。読み直しに失敗した (status が success で
 * ない) ときと、キャッシュに無いときは何もしない (キャッシュは前の値のまま残っている)
 */
const reapplySummaryOverrides = (
  queryClient: QueryClient,
  run: string,
  holder: SummaryOverridesHolder,
): void => {
  const queryKey = summaryQueryKey(run);
  const summary = queryClient.getQueryData<RunSummary>(queryKey);
  if (summary === undefined || queryClient.getQueryState(queryKey)?.status !== "success") {
    return;
  }
  const applied = applySummaryOverrides(summary, holder.current);
  if (applied !== summary) {
    queryClient.setQueryData(queryKey, applied);
  }
};

/**
 * 届いた順のイベントを実行 run のキャッシュに足し、applyLiveEvents の結果を返す。
 * - summary と 4 つの区間 API は、変わったものだけを setQueryData で書き換える
 * - advisor_step は、advisor の実行ごとの途中経過の置き場 (streamedStepsQueryKey) の末尾に、届いた順に足す
 *   (advisor の実行の詳細がキャッシュに無くても足す。詳細の表示は mergeAdvisorSteps で詳細の steps とつなぐ)
 * - 読み直すキャッシュ (invalidations) を invalidateQueries で無効にする (使っている query は読み直される)
 * - overrides を渡すと、イベントのうち summary 向けのもの (advisor_run・note_version・stats) の最新の値をそこに足し、
 *   summary の読み直しが終わった後に、その時点の値を読み直した summary に当て直す (読み直しの応答は、読み直しを
 *   始めた時点のサーバーの値のため、その間に足した値を含まないことがある)
 */
export const writeLiveEvents = (
  queryClient: QueryClient,
  run: string,
  events: readonly LiveEvent[],
  overrides?: SummaryOverridesHolder,
): LiveMergeResult => {
  if (overrides !== undefined) {
    overrides.current = recordSummaryOverrides(overrides.current, events);
  }
  const before = readLiveCaches(queryClient, run);
  const result = applyLiveEvents(before, events);
  const after = result.caches;
  if (after.summary !== undefined && after.summary !== before.summary) {
    queryClient.setQueryData(summaryQueryKey(run), after.summary);
  }
  writeRange(queryClient, run, "requests", before.requests, after.requests);
  writeRange(queryClient, run, "frames", before.frames, after.frames);
  writeRange(queryClient, run, "pad", before.pad, after.pad);
  writeRange(queryClient, run, "events", before.events, after.events);
  for (const [name, steps] of result.advisorSteps) {
    queryClient.setQueryData<AdvisorStep[]>(streamedStepsQueryKey(run, name), (streamed) => [
      ...(streamed ?? []),
      ...steps,
    ]);
  }
  for (const invalidation of result.invalidations) {
    // invalidateQueries は、使っている query の読み直しが終わる (失敗を含む) と resolve する
    const refetched = queryClient.invalidateQueries({
      queryKey: invalidationQueryKey(run, invalidation),
      exact: true,
    });
    if (invalidation.type === "summary" && overrides !== undefined) {
      void refetched.then(() => reapplySummaryOverrides(queryClient, run, overrides));
    }
  }
  return result;
};

/**
 * 実行 run の advisor の途中経過の置き場を、すべて空にする (/live を開くたびに使う。サーバーは接続した後に追記された
 * 行の途中経過だけを送り、画面はつないで最初のイベントで advisor の実行の詳細をすべて読み直すため)
 */
export const clearStreamedSteps = (queryClient: QueryClient, run: string): void => {
  queryClient.setQueriesData<AdvisorStep[]>({ queryKey: streamedStepsQueryKeyPrefix(run) }, []);
};

/** 実行 run の advisor の途中経過の置き場を捨てる (実行を選び直したときに使う) */
export const discardStreamedSteps = (queryClient: QueryClient, run: string): void => {
  queryClient.removeQueries({ queryKey: streamedStepsQueryKeyPrefix(run) });
};

/**
 * end を受け取った後に、実行 run の summary とすべての要求の詳細を無効にする (使っている query は読み直される)。
 * summary は記録の有無・読み飛ばした行の数・実行中かを最新にするため。要求の詳細は、要求の行が書かれて decision の
 * 行がまだ書かれていない間に読んだ詳細 (所要時間などが無い) が、次の要求が来ないまま残らないようにするため
 */
export const invalidateAfterEnd = (queryClient: QueryClient, run: string): void => {
  void queryClient.invalidateQueries({ queryKey: summaryQueryKey(run), exact: true });
  void queryClient.invalidateQueries({ queryKey: requestQueryKeyPrefix(run) });
};

/**
 * 実行 run の区間 API のキャッシュのどれかに、イベントで足した記録があるか (あれば、つなぐ前に区間 API を
 * 読み直す)
 */
export const hasAppendedRecords = (queryClient: QueryClient, run: string): boolean =>
  RANGE_KINDS.some((kind) => {
    const range = getRange(queryClient, run, kind);
    return range !== undefined && appendedRanges.has(range);
  });

/**
 * /live のクエリーの位置を、実行 run の区間 API のキャッシュの cursor から返す (events の cursor が run.jsonl の
 * 位置)。キャッシュに無い種類は null
 */
export const liveCursorsOf = (queryClient: QueryClient, run: string): LiveCursors => ({
  run: getRange(queryClient, run, "events")?.cursor ?? null,
  requests: getRange(queryClient, run, "requests")?.cursor ?? null,
  frames: getRange(queryClient, run, "frames")?.cursor ?? null,
  pad: getRange(queryClient, run, "pad")?.cursor ?? null,
});

/** 実行 run の summary と 4 つの区間 API の queryKey */
const runRecordQueryKeys = (run: string) => [
  summaryQueryKey(run),
  ...RANGE_KINDS.map((kind) => rangeQueryKey(run, kind)),
];

/**
 * 実行 run の summary と 4 つの区間 API のキャッシュを、古いものとして印を付ける (今は読み直さない)。ライブで追うのを
 * やめた後もサーバーでは記録が書き足されるため、次にその実行の query を使い始めたときに読み直させる
 */
export const markRunRecordsStale = (queryClient: QueryClient, run: string): void => {
  for (const queryKey of runRecordQueryKeys(run)) {
    void queryClient.invalidateQueries({ queryKey, exact: true, refetchType: "none" });
  }
};

/**
 * 実行 run の summary と 4 つの区間 API のキャッシュのどれかに、古いものとしての印が付いているか (あれば、つなぐ前に
 * 読み直す。読み直すと印は消える)
 */
export const hasStaleRecords = (queryClient: QueryClient, run: string): boolean =>
  runRecordQueryKeys(run).some(
    (queryKey) => queryClient.getQueryState(queryKey)?.isInvalidated === true,
  );

/**
 * 実行 run の summary と 4 つの区間 API を読み直す (読み込み中の古い要求は取り消す)。すべて読めたら true、
 * どれかが読めなければ false (読めなかった query は、前に読めた値を data に残す)
 */
export const refetchRunRecords = async (
  queryClient: QueryClient,
  run: string,
): Promise<boolean> => {
  const queryKeys = runRecordQueryKeys(run);
  try {
    await Promise.all(
      queryKeys.map((queryKey) =>
        queryClient.refetchQueries({ queryKey, exact: true }, { throwOnError: true }),
      ),
    );
    return true;
  } catch {
    return false;
  }
};
