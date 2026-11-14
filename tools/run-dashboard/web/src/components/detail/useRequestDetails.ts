// 複数の要求の詳細を TanStack Query の useQueries でまとめて読むフック (フレームを載せた要求を探すときに使う)。
// queryKey と読み方は useRequestDetail と同じにして、読んだ値を共有する。
import { useQueries } from "@tanstack/react-query";
import { requestDetailPath } from "../../api/apiPaths.ts";
import type { RequestDetail, RequestDetailOldFormat } from "../../api/apiTypes.ts";
import { fetchJson } from "../../api/fetchJson.ts";

/** 複数の要求の詳細の読み込みの状態。details は numbers と同じ並び (読めていない要求は undefined) */
export type RequestDetailsState = {
  details: (RequestDetail | RequestDetailOldFormat | undefined)[];
  /** どれかを読み込み中 */
  pending: boolean;
  /** 読めなかった要求の最初の理由。無ければ null */
  error: unknown;
  /** 読めなかった要求を読み直す */
  retry: () => void;
};

/** 実行 run の、番号 numbers の要求の詳細を読む。queryKey は ["request", run, n] */
export const useRequestDetails = (run: string, numbers: readonly number[]): RequestDetailsState =>
  useQueries({
    queries: numbers.map((n) => ({
      queryKey: ["request", run, n],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        fetchJson<RequestDetail | RequestDetailOldFormat>(requestDetailPath(run, n), signal),
    })),
    combine: (results) => {
      const failed = results.filter((result) => result.status === "error");
      return {
        details: results.map((result) => result.data),
        pending: results.some((result) => result.status === "pending"),
        error: failed[0]?.error ?? null,
        retry: () => {
          for (const result of failed) {
            void result.refetch();
          }
        },
      };
    },
  });
