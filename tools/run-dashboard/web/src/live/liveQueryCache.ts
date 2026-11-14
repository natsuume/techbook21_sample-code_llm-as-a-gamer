// ライブで使う、TanStack Query のキャッシュの読み書き。届いたイベントを summary・区間 API・advisor の実行の詳細の
// キャッシュに足し、読み直すキャッシュを無効にする。/live につなぐ位置 (cursor) を区間 API のキャッシュから読み、
// 区間 API と summary を読み直す。
import type { QueryClient } from "@tanstack/react-query";
import type { RangeKind } from "../api/apiPaths.ts";
import type {
  AdvisorDetail,
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
  summaryQueryKey,
} from "../api/queries.ts";
import {
  appendAdvisorSteps,
  applyLiveEvents,
  type LiveCaches,
  type LiveInvalidation,
  type LiveMergeResult,
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
 * 届いた順のイベントを実行 run のキャッシュに足し、applyLiveEvents の結果を返す。
 * - summary と 4 つの区間 API は、変わったものだけを setQueryData で書き換える
 * - advisor_step は、キャッシュにある advisor の実行の詳細に appendAdvisorSteps で足す (無ければ何もしない)
 * - 読み直すキャッシュ (invalidations) を invalidateQueries で無効にする (使っている query は読み直される)
 */
export const writeLiveEvents = (
  queryClient: QueryClient,
  run: string,
  events: readonly LiveEvent[],
): LiveMergeResult => {
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
    const queryKey = advisorQueryKey(run, name);
    const detail = queryClient.getQueryData<AdvisorDetail>(queryKey);
    if (detail === undefined) {
      continue;
    }
    const appended = appendAdvisorSteps(detail, steps);
    if (appended !== detail) {
      queryClient.setQueryData(queryKey, appended);
    }
  }
  for (const invalidation of result.invalidations) {
    void queryClient.invalidateQueries({
      queryKey: invalidationQueryKey(run, invalidation),
      exact: true,
    });
  }
  return result;
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
