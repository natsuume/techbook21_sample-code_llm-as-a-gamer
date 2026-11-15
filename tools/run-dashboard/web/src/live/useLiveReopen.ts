// 終わった実行 (実行中でない実行を選んだか、end を受け取った) が実行中に戻ったら、区間 API と summary を読み直して
// ライブで開き直すフック。実行の一覧の読み直しの結果で決める。
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { runsQueryKey, useRuns } from "../api/queries.ts";
import { useViewState } from "../state/ViewStateContext.tsx";
import { refetchRunRecords } from "./liveQueryCache.ts";
import { reopenOutcome, shouldReopenLive } from "./liveStatus.ts";

/**
 * 選んでいる実行の liveSession が none か ended になったら、その時刻を覚えて実行の一覧を読み直す (読み込み中の古い
 * 要求は取り消すため、この後に読み終えた一覧は、終わった後のサーバーの値になる)。一覧を読み終えるたびに
 * shouldReopenLive で決め、開き直すなら summary と区間 API を読み直し、読み直しが終わった後にすることを
 * reopenOutcome で決める。すべて読み直せたら reopenLive を送り、覚えた時刻を捨てる (開き直してすぐに end が届いたら、
 * もう一度時刻を覚え、次に読み終えた一覧で決める)。どれかが読めなければ reopenLive を送らず、覚えた時刻を戻して、
 * 次に読み終えた一覧でもう一度決める。RunViewProvider の中で 1 回だけ呼ぶ
 */
export const useLiveReopen = (): void => {
  const { state, dispatch } = useViewState();
  const queryClient = useQueryClient();
  const runs = useRuns();
  const run = state.run;
  const liveSession = state.liveSession;
  const inactiveSince = useRef<number | null>(null);
  // 読み直しを待つ間に、選んでいる実行や liveSession が変わったかを確かめるための、最後の描画の値
  const latest = useRef({ run, liveSession });
  useEffect(() => {
    latest.current = { run, liveSession };
  });

  useEffect(() => {
    if (run === null || liveSession === "active") {
      inactiveSince.current = null;
      return;
    }
    inactiveSince.current = Date.now();
    void queryClient.refetchQueries({ queryKey: runsQueryKey(), exact: true });
  }, [run, liveSession, queryClient]);

  const listedInProgress =
    run === null ? null : (runs.data?.find((item) => item.run === run)?.in_progress ?? null);
  const readAt = runs.dataUpdatedAt;
  useEffect(() => {
    if (run === null || listedInProgress === null) {
      return;
    }
    const listed = { inProgress: listedInProgress, readAt };
    const since = inactiveSince.current;
    if (!shouldReopenLive(listed, liveSession, since)) {
      return;
    }
    // 読み直しを待つ間は、次に読み終えた一覧で重ねて開き直さない
    inactiveSince.current = null;
    void refetchRunRecords(queryClient, run)
      .catch(() => false)
      .then((reloaded) => {
        switch (reopenOutcome(reloaded, run, latest.current)) {
          case "reopen":
            dispatch({ type: "reopenLive" });
            return;
          case "waitForNextList":
            // 終わった時刻を戻し、次に読み終えた一覧でもう一度決める
            if (inactiveSince.current === null) {
              inactiveSince.current = since;
            }
            return;
          case "ignore":
            return;
        }
      });
  }, [run, listedInProgress, readAt, liveSession, queryClient, dispatch]);
};
