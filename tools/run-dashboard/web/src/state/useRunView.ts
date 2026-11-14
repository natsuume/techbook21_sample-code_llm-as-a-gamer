// 選んでいる実行の summary と区間 API (requests・frames・pad・events) を読み、画面全体で使う値を 1 回だけ
// 作って配るためのフック。値を作るのは RunViewProvider の中の useRunViewSource だけで、各区画は useRunView で読む。
import { useQueryClient } from "@tanstack/react-query";
import { createContext, use, useCallback, useEffect, useMemo, useState } from "react";
import { useRangeItems, useRunSummary } from "../api/queries.ts";
import { firstRequestN } from "../requests/requestNavigation.ts";
import { buildRunData, type PlaybackSelection, type RunData, selectPlayback } from "./runView.ts";
import { useViewState } from "./ViewStateContext.tsx";

/** 選んでいる実行の、画面全体で使う値 (RunData と PlaybackSelection を参照) */
export type RunView = RunData & PlaybackSelection & { run: string };

/**
 * 選んでいる実行の読み込みの状態。
 * - noRun: 実行を選んでいない
 * - loading: summary と区間 API のどれかを読み込み中か、最初の要求を選ぶ前
 * - error: summary と区間 API のどれかが読めなかった。retry でこの実行の失敗した query を読み直す
 * - ready: すべて読めて、最初の要求を選んだ (runLoaded を送った) 後
 */
export type RunViewState =
  | { status: "noRun" }
  | { status: "loading"; run: string }
  | { status: "error"; run: string; error: unknown; retry: () => void }
  | { status: "ready"; run: string; view: RunView };

/** RunViewProvider が配る値 */
export const RunViewContext = createContext<RunViewState | null>(null);

/**
 * 選んでいる実行の summary と区間 API を読み、RunViewState を作る (RunViewProvider の中で 1 回だけ呼ぶ)。
 * - 選択によらない値 (buildRunData) は読んだ値が変わったときだけ、選択による値 (selectPlayback) は
 *   selectedN と width が変わったときだけ useMemo で作り直す
 * - 要求の区間 API が届いたら、実行ごとに 1 回だけ runLoaded (firstRequestN(items)、available が false なら null)
 *   を送る。実行を選び直したら、もう一度送る
 */
export const useRunViewSource = (): RunViewState => {
  const { state, dispatch } = useViewState();
  const run = state.run;
  const summaryQuery = useRunSummary(run);
  const requestsQuery = useRangeItems(run, "requests");
  const framesQuery = useRangeItems(run, "frames");
  const padQuery = useRangeItems(run, "pad");
  const eventsQuery = useRangeItems(run, "events");

  // runLoaded を送った実行。選んでいる実行が変わったら、描画の中で null に戻す (前の描画の実行を覚えて比べる)
  const [loadedRun, setLoadedRun] = useState<string | null>(null);
  const [previousRun, setPreviousRun] = useState<string | null>(run);
  if (previousRun !== run) {
    setPreviousRun(run);
    setLoadedRun(null);
  }

  const requests = requestsQuery.data;
  useEffect(() => {
    if (run === null || requests === undefined || loadedRun === run) {
      return;
    }
    dispatch({
      type: "runLoaded",
      firstN: requests.available ? firstRequestN(requests.items) : null,
    });
    setLoadedRun(run);
  }, [run, requests, loadedRun, dispatch]);

  const summary = summaryQuery.data;
  const frames = framesQuery.data;
  const pad = padQuery.data;
  const events = eventsQuery.data;
  const runData = useMemo(
    () =>
      summary !== undefined &&
      requests !== undefined &&
      frames !== undefined &&
      pad !== undefined &&
      events !== undefined
        ? buildRunData({ summary, requests, frames, pad, events })
        : null,
    [summary, requests, frames, pad, events],
  );
  const selection = useMemo(
    () => (runData === null ? null : selectPlayback(runData, state.selectedN, state.width)),
    [runData, state.selectedN, state.width],
  );

  const failedQuery = [summaryQuery, requestsQuery, framesQuery, padQuery, eventsQuery].find(
    (query) => query.status === "error",
  );
  const hasError = failedQuery !== undefined;
  const error: unknown = failedQuery?.error;

  const queryClient = useQueryClient();
  const retry = useCallback(() => {
    void queryClient.refetchQueries({
      type: "active",
      predicate: (query) => query.state.status === "error" && query.queryKey[1] === run,
    });
  }, [queryClient, run]);

  return useMemo((): RunViewState => {
    if (run === null) {
      return { status: "noRun" };
    }
    if (runData !== null && selection !== null && loadedRun === run) {
      return { status: "ready", run, view: { ...runData, ...selection, run } };
    }
    if (hasError) {
      return { status: "error", run, error, retry };
    }
    return { status: "loading", run };
  }, [run, runData, selection, loadedRun, hasError, error, retry]);
};

/** 選んでいる実行の読み込みの状態と、画面全体で使う値を返す。RunViewProvider の外で呼ぶと例外を投げる */
export const useRunView = (): RunViewState => {
  const value = use(RunViewContext);
  if (value === null) {
    throw new Error("useRunView は RunViewProvider の中で使う");
  }
  return value;
};
