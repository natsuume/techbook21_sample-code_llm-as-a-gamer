// run-dashboard のサーバーの API を読む TanStack Query のフック。終わった実行の記録は変わらないため、
// 一度読んだ値を古くならないものとして使い続ける。
import { QueryClient, skipToken, type UseQueryResult, useQuery } from "@tanstack/react-query";
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

/**
 * 画面の QueryClient を作る。すべての query を staleTime: Infinity (読み直さない)、
 * refetchOnWindowFocus: false、retry: 1 (失敗したら 1 回だけ読み直す) にする
 */
export const createQueryClient = (): QueryClient =>
  new QueryClient({
    defaultOptions: {
      queries: { staleTime: Number.POSITIVE_INFINITY, refetchOnWindowFocus: false, retry: 1 },
    },
  });

/** 実行の一覧 (/api/runs) を読む。queryKey は ["runs"] */
export const useRuns = (): UseQueryResult<RunListItem[]> =>
  useQuery({
    queryKey: ["runs"],
    queryFn: ({ signal }) => fetchJson<RunListItem[]>(runsPath(), signal),
  });

/** 実行の summary を読む。queryKey は ["summary", run]。run が null なら読まない */
export const useRunSummary = (run: string | null): UseQueryResult<RunSummary> =>
  useQuery({
    queryKey: ["summary", run],
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
    queryKey: ["range", run, kind],
    queryFn:
      run === null
        ? skipToken
        : ({ signal }) => fetchJson<RangeResponse<RangeItemOf[K]>>(rangePath(run, kind), signal),
  });

/**
 * 要求の詳細を読む。queryKey は ["request", run, n]。古い形の実行では RequestDetailOldFormat が返る
 * (isOldFormatRequestDetail で判定する)。run か n が null なら読まない
 */
export const useRequestDetail = (
  run: string | null,
  n: number | null,
): UseQueryResult<RequestDetail | RequestDetailOldFormat> =>
  useQuery({
    queryKey: ["request", run, n],
    queryFn:
      run === null || n === null
        ? skipToken
        : ({ signal }) =>
            fetchJson<RequestDetail | RequestDetailOldFormat>(requestDetailPath(run, n), signal),
  });

/** ゲームの情報の版を読む。queryKey は ["note", run, version]。run か version が null なら読まない */
export const useNoteDetail = (
  run: string | null,
  version: number | null,
): UseQueryResult<NoteDetail> =>
  useQuery({
    queryKey: ["note", run, version],
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
    queryKey: ["advisor", run, name],
    queryFn:
      run === null || name === null
        ? skipToken
        : ({ signal }) => fetchJson<AdvisorDetail>(advisorPath(run, name), signal),
  });
