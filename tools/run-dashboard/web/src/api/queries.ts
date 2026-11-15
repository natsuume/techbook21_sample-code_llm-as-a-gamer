// run-dashboard のサーバーの API を読む TanStack Query のフックと、その queryKey を作る関数。読んだ値は古くならない
// ものとして使い続け、実行中の実行の記録は、ライブのイベントをキャッシュに足すか、無効にして読み直して最新にする。
// 実行の一覧だけは、新しく始まった実行と実行中かどうかを知るため、一定の間隔で読み直す。
// ライブで届いた advisor の途中経過は、API を読まない置き場 (キャッシュの別の queryKey) に置き、そこから読む。
import {
  keepPreviousData,
  QueryClient,
  skipToken,
  type UseQueryResult,
  useQuery,
} from "@tanstack/react-query";
import { RUN_LIST_REFRESH_MS } from "../live/liveTiming.ts";
import {
  advisorPath,
  notePath,
  type RangeKind,
  rangePath,
  requestDetailPath,
  runsPath,
  summaryPath,
} from "./apiPaths.ts";
import type {
  AdvisorDetail,
  AdvisorStep,
  EventItem,
  FrameItem,
  NoteDetail,
  PadItem,
  RangeResponse,
  RequestDetail,
  RequestDetailOldFormat,
  RequestItem,
  RunListItem,
  RunSummary,
} from "./apiTypes.ts";
import { fetchJson } from "./fetchJson.ts";

/** 区間 API の種類ごとの、items の 1 件の型 */
export type RangeItemOf = {
  requests: RequestItem;
  frames: FrameItem;
  pad: PadItem;
  events: EventItem;
};

/** 区間 API の種類のすべて */
export const RANGE_KINDS: readonly RangeKind[] = ["requests", "frames", "pad", "events"];

/** 実行の一覧の queryKey ["runs"] */
export const runsQueryKey = (): readonly ["runs"] => ["runs"];
/** 実行の summary の queryKey ["summary", run] */
export const summaryQueryKey = (run: string | null): readonly ["summary", string | null] => [
  "summary",
  run,
];
/** 区間 API の queryKey ["range", run, kind] */
export const rangeQueryKey = (
  run: string | null,
  kind: RangeKind,
): readonly ["range", string | null, RangeKind] => ["range", run, kind];
/** 要求の詳細の queryKey ["request", run, n] */
export const requestQueryKey = (
  run: string | null,
  n: number | null,
): readonly ["request", string | null, number | null] => ["request", run, n];
/** ゲームの情報の版の queryKey ["note", run, version] */
export const noteQueryKey = (
  run: string | null,
  version: number | null,
): readonly ["note", string | null, number | null] => ["note", run, version];
/** advisor の実行の詳細の queryKey ["advisor", run, name] */
export const advisorQueryKey = (
  run: string | null,
  name: string | null,
): readonly ["advisor", string | null, string | null] => ["advisor", run, name];
/** 実行 run の、すべての advisor の実行の詳細に当たる queryKey の前半 ["advisor", run] */
export const advisorQueryKeyPrefix = (run: string): readonly ["advisor", string] => [
  "advisor",
  run,
];
/** 実行 run の、すべての要求の詳細に当たる queryKey の前半 ["request", run] */
export const requestQueryKeyPrefix = (run: string): readonly ["request", string] => [
  "request",
  run,
];

/** ライブで届いた advisor の途中経過の置き場の queryKey の先頭 */
const STREAMED_STEPS_KEY = "liveAdvisorSteps";
/**
 * ライブで届いた advisor の途中経過 (AdvisorStep の配列、届いた順) の置き場の queryKey ["liveAdvisorSteps", run, name]。
 * API を読む query ではなく、キャッシュに置くだけ (advisor の実行の詳細の query とは別のため、詳細の無効化と読み直しで
 * 変わらない)
 */
export const streamedStepsQueryKey = (
  run: string | null,
  name: string | null,
): readonly ["liveAdvisorSteps", string | null, string | null] => [STREAMED_STEPS_KEY, run, name];
/** 実行 run の、すべての advisor の途中経過の置き場に当たる queryKey の前半 ["liveAdvisorSteps", run] */
export const streamedStepsQueryKeyPrefix = (run: string): readonly ["liveAdvisorSteps", string] => [
  STREAMED_STEPS_KEY,
  run,
];

/**
 * 画面の QueryClient を作る。すべての query を staleTime: Infinity (読み直さない)、
 * refetchOnWindowFocus: false、retry: 1 (失敗したら 1 回だけ読み直す) にする。
 * ライブで届いた advisor の途中経過の置き場は、使っている画面が無い間も捨てない (gcTime: Infinity。読み直して
 * 戻せないため)
 */
export const createQueryClient = (): QueryClient => {
  const client = new QueryClient({
    defaultOptions: {
      queries: { staleTime: Number.POSITIVE_INFINITY, refetchOnWindowFocus: false, retry: 1 },
    },
  });
  client.setQueryDefaults([STREAMED_STEPS_KEY], { gcTime: Number.POSITIVE_INFINITY });
  return client;
};

/**
 * 実行の一覧 (/api/runs) を読む。queryKey は ["runs"]。RUN_LIST_REFRESH_MS ごとに読み直す
 * (画面が隠れている間は止まる)。読み直しに失敗しても、前に読めた一覧は data に残る
 */
export const useRuns = (): UseQueryResult<RunListItem[]> =>
  useQuery({
    queryKey: runsQueryKey(),
    queryFn: ({ signal }) => fetchJson<RunListItem[]>(runsPath(), signal),
    refetchInterval: RUN_LIST_REFRESH_MS,
  });

/** 実行の summary を読む。queryKey は ["summary", run]。run が null なら読まない */
export const useRunSummary = (run: string | null): UseQueryResult<RunSummary> =>
  useQuery({
    queryKey: summaryQueryKey(run),
    queryFn:
      run === null ? skipToken : ({ signal }) => fetchJson<RunSummary>(summaryPath(run), signal),
  });

/**
 * 区間 API (requests・frames・pad・events) を from・to なしで読む。queryKey は ["range", run, kind]。
 * 応答の items の型は kind で決まる (RangeItemOf)。run が null なら読まない
 */
export const useRangeItems = <K extends RangeKind>(
  run: string | null,
  kind: K,
): UseQueryResult<RangeResponse<RangeItemOf[K]>> =>
  useQuery({
    queryKey: rangeQueryKey(run, kind),
    queryFn:
      run === null
        ? skipToken
        : ({ signal }) => fetchJson<RangeResponse<RangeItemOf[K]>>(rangePath(run, kind), signal),
  });

/**
 * 要求の詳細を読む。queryKey は ["request", run, n]。古い形の実行では RequestDetailOldFormat が返る
 * (isOldFormatRequestDetail で判定する)。run か n が null なら読まない。
 * n を変えたとき、新しい要求の詳細が届くまで前の要求の詳細を data に残す (placeholderData: keepPreviousData。
 * ライブで最新を追う間、骨組みに戻さないため)。そのため data の n は、渡した n と違うことがある
 */
export const useRequestDetail = (
  run: string | null,
  n: number | null,
): UseQueryResult<RequestDetail | RequestDetailOldFormat> =>
  useQuery({
    queryKey: requestQueryKey(run, n),
    queryFn:
      run === null || n === null
        ? skipToken
        : ({ signal }) =>
            fetchJson<RequestDetail | RequestDetailOldFormat>(requestDetailPath(run, n), signal),
    placeholderData: keepPreviousData,
  });

/** ゲームの情報の版を読む。queryKey は ["note", run, version]。run か version が null なら読まない */
export const useNoteDetail = (
  run: string | null,
  version: number | null,
): UseQueryResult<NoteDetail> =>
  useQuery({
    queryKey: noteQueryKey(run, version),
    queryFn:
      run === null || version === null
        ? skipToken
        : ({ signal }) => fetchJson<NoteDetail>(notePath(run, version), signal),
  });

/** advisor の実行の詳細を読む。queryKey は ["advisor", run, name]。run か name が null なら読まない */
export const useAdvisorDetail = (
  run: string | null,
  name: string | null,
): UseQueryResult<AdvisorDetail> =>
  useQuery({
    queryKey: advisorQueryKey(run, name),
    queryFn:
      run === null || name === null
        ? skipToken
        : ({ signal }) => fetchJson<AdvisorDetail>(advisorPath(run, name), signal),
  });

/** 届いた途中経過が無いときに返す、変わらない空の配列 */
const NO_STEPS: readonly AdvisorStep[] = [];

/**
 * ライブで届いた advisor の実行 name の途中経過 (届いた順) を、置き場 ["liveAdvisorSteps", run, name] から読む
 * (API は読まない。置き場はライブの接続が書く)。置き場が無ければ空の配列
 */
export const useStreamedAdvisorSteps = (
  run: string | null,
  name: string | null,
): readonly AdvisorStep[] =>
  useQuery<AdvisorStep[]>({ queryKey: streamedStepsQueryKey(run, name), queryFn: skipToken })
    .data ?? NO_STEPS;
