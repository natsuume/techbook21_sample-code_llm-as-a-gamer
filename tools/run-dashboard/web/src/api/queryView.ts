// TanStack Query の結果を、画面の区画が分岐に使う 4 つの状態 (idle・loading・error・ready) にまとめる純粋な関数。

/** toQueryView が読む、TanStack Query の結果の一部 */
export type QueryResultLike<T> = {
  status: "pending" | "error" | "success";
  fetchStatus: "fetching" | "paused" | "idle";
  data: T | undefined;
  error: unknown;
  refetch: () => unknown;
};

/**
 * 区画の状態。idle は読む対象が無い (引数が null で読み込みを止めている)、loading は読み込み中、
 * error は失敗 (retry で読み直す)、ready は読めた値
 */
export type QueryView<T> =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "error"; error: unknown; retry: () => void }
  | { state: "ready"; data: T };

/**
 * TanStack Query の結果を QueryView にする。
 * - status が "error" なら error (retry は refetch を呼ぶ)
 * - status が "success" なら ready (data は undefined でない値)
 * - status が "pending" で fetchStatus が "idle" なら idle (読み込みを止めた query は、データが無いまま
 *   pending のため、loading と区別する)。それ以外の pending は loading
 */
export const toQueryView = <T>(query: QueryResultLike<T>): QueryView<T> => {
  if (query.status === "error") {
    return {
      state: "error",
      error: query.error,
      retry: () => {
        void query.refetch();
      },
    };
  }
  if (query.status === "success" && query.data !== undefined) {
    return { state: "ready", data: query.data };
  }
  return query.fetchStatus === "idle" ? { state: "idle" } : { state: "loading" };
};
