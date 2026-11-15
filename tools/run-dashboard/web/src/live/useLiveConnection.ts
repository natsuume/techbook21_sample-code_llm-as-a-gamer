// 選んでいる実行をライブで見る間 (liveSession が active)、/live にサーバー送信イベント (EventSource) でつなぎ、届いた
// 記録を TanStack Query のキャッシュに 1 秒に 4 回までまとめて足すフック。切れたら区間 API と summary を読み直して
// つなぎ直し (EventSource の自動のつなぎ直しは使わない)、end を受け取ったら終わった実行の表示に替える。
import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { livePath } from "../api/apiPaths.ts";
import {
  LIVE_EVENT_KINDS,
  type LiveEvent,
  type LiveEventKind,
  type RangeResponse,
  type RequestItem,
} from "../api/apiTypes.ts";
import {
  advisorQueryKeyPrefix,
  rangeQueryKey,
  summaryQueryKey,
  useRangeItems,
  useRunSummary,
} from "../api/queries.ts";
import { latestRequestN } from "../state/runView.ts";
import { useViewState } from "../state/ViewStateContext.tsx";
import type { LiveEndReason, LiveSession } from "../state/viewState.ts";
import { parseLiveEvent } from "./liveEvents.ts";
import {
  hasAppendedRecords,
  hasStaleRecords,
  liveCursorsOf,
  markRunRecordsStale,
  refetchRunRecords,
  writeLiveEvents,
} from "./liveQueryCache.ts";
import type { LiveConnection } from "./liveStatus.ts";
import { flushIntervalMs, nextFlushDelayMs, reconnectDelayMs } from "./liveTiming.ts";

/** 接続のセッションが呼び出し側に知らせること */
type LiveSessionCallbacks = {
  /** 接続の状態 (connecting・open・reconnecting) が変わった */
  onConnection: (connection: LiveConnection) => void;
  /** end を受け取った。latestN は記録を足した後の最後の要求の n */
  onEnded: (recordEnd: boolean, latestN: number | null) => void;
  /** 今の、記録を足す最小の間隔 (ミリ秒)。足しを予約するたびに呼ぶ */
  flushIntervalMs: () => number;
};

/**
 * 実行 run の /live につなぐセッションを始め、閉じる関数を返す。
 * - つなぐ位置は区間 API のキャッシュの cursor。キャッシュにイベントで足した記録があるか、キャッシュに古いものとしての
 *   印 (markRunRecordsStale) が付いていれば、先に読み直してからつなぐ (読み直しとつないだ後の足し込みを重ねない)
 * - イベントの種類ごとに受け取り、parseLiveEvent で読めたものをためる。ためた分は nextFlushDelayMs (間隔は
 *   callbacks.flushIntervalMs()) の時刻に 1 回で writeLiveEvents で足す
 * - つないで最初のイベントで open にし、advisor の実行の詳細をすべて無効にする (つなぐ前に読んだ詳細に、つなぐまでの
 *   間の step が欠けないようにするため)
 * - end: ためた分をすぐ足して閉じ、summary を無効にして onEnded
 * - error: すぐ閉じ (自動のつなぎ直しを使わない)、ためた分を足して reconnecting にする。reconnectDelayMs(失敗の
 *   回数) 待って summary と区間 API を読み直し、読めたら新しい cursor でつなぐ。読めないか、つないで最初の
 *   イベントの前に切れたら、失敗の回数を足して繰り返す
 * - 閉じたら、ためた分と待ちを捨て、古い接続のイベントは接続ごとの番号で見分けて捨てる
 */
const openLiveSession = (
  queryClient: QueryClient,
  run: string,
  callbacks: LiveSessionCallbacks,
): (() => void) => {
  let closed = false;
  let connectionId = 0;
  let source: EventSource | null = null;
  let buffered: LiveEvent[] = [];
  let lastFlushAt: number | null = null;
  let flushTimer: ReturnType<typeof setTimeout> | undefined;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;
  let failures = 0;

  const flush = (): void => {
    clearTimeout(flushTimer);
    flushTimer = undefined;
    if (buffered.length === 0) {
      return;
    }
    const events = buffered;
    buffered = [];
    lastFlushAt = performance.now();
    writeLiveEvents(queryClient, run, events);
  };

  const scheduleFlush = (): void => {
    if (flushTimer === undefined) {
      flushTimer = setTimeout(
        flush,
        nextFlushDelayMs(lastFlushAt, performance.now(), callbacks.flushIntervalMs()),
      );
    }
  };

  const closeSource = (): void => {
    connectionId += 1;
    source?.close();
    source = null;
  };

  const reconnectLater = (): void => {
    const delay = reconnectDelayMs(failures);
    callbacks.onConnection({ state: "reconnecting", failures, retryInMs: delay });
    retryTimer = setTimeout(() => {
      retryTimer = undefined;
      void reloadAndConnect();
    }, delay);
  };

  const reloadAndConnect = async (): Promise<void> => {
    const reloaded = await refetchRunRecords(queryClient, run);
    if (closed) {
      return;
    }
    if (reloaded) {
      connect();
      return;
    }
    failures += 1;
    reconnectLater();
  };

  const finish = (recordEnd: boolean): void => {
    flush();
    closeSource();
    const requests = queryClient.getQueryData<RangeResponse<RequestItem>>(
      rangeQueryKey(run, "requests"),
    );
    void queryClient.invalidateQueries({ queryKey: summaryQueryKey(run), exact: true });
    callbacks.onEnded(recordEnd, requests === undefined ? null : latestRequestN({ requests }));
  };

  const connect = (): void => {
    closeSource();
    const id = connectionId;
    const eventSource = new EventSource(livePath(run, liveCursorsOf(queryClient, run)));
    source = eventSource;
    let received = false;

    const receive = (kind: LiveEventKind, message: MessageEvent): void => {
      const data: unknown = message.data;
      if (closed || id !== connectionId || typeof data !== "string") {
        return;
      }
      const event = parseLiveEvent(kind, data);
      if (event === null) {
        return;
      }
      if (!received) {
        received = true;
        failures = 0;
        callbacks.onConnection({ state: "open" });
        void queryClient.invalidateQueries({ queryKey: advisorQueryKeyPrefix(run) });
      }
      if (event.kind === "end") {
        finish(event.data.record_end);
        return;
      }
      buffered.push(event);
      scheduleFlush();
    };
    for (const kind of LIVE_EVENT_KINDS) {
      eventSource.addEventListener(kind, (message) => receive(kind, message));
    }
    eventSource.addEventListener("error", () => {
      if (closed || id !== connectionId) {
        return;
      }
      if (!received) {
        failures += 1;
      }
      flush();
      closeSource();
      reconnectLater();
    });
  };

  callbacks.onConnection({ state: "connecting" });
  if (hasAppendedRecords(queryClient, run) || hasStaleRecords(queryClient, run)) {
    void reloadAndConnect();
  } else {
    connect();
  }

  return () => {
    closed = true;
    closeSource();
    clearTimeout(flushTimer);
    clearTimeout(retryTimer);
    buffered = [];
  };
};

const IDLE: LiveConnection = { state: "idle" };
const CONNECTING: LiveConnection = { state: "connecting" };

/**
 * 画面に出す接続の状態。liveSession が none なら idle、ended なら ended (endReason が recordEnd か)、active なら
 * セッションの状態
 */
const liveConnectionOf = (
  liveSession: LiveSession,
  endReason: LiveEndReason | null,
  session: LiveConnection,
): LiveConnection => {
  switch (liveSession) {
    case "none":
      return IDLE;
    case "ended":
      return { state: "ended", recordEnd: endReason === "recordEnd" };
    case "active":
      return session;
  }
};

/**
 * 選んでいる実行の /live への接続を管理し、接続の状態を返す (RunViewProvider の中で 1 回だけ呼び、
 * LiveConnectionContext で配る)。liveSession が active で、summary と 4 つの区間 API が読めたらつなぐ。
 * 実行を選び直す・liveSession が active でなくなる・アンマウントで閉じる。一時停止中も、終わった実行の表示中も、
 * 届いた記録はキャッシュに足し続ける。足す間隔は、表示する幅の今の値で flushIntervalMs が決める (幅を変えても
 * つなぎ直さない)。end を受け取ったら liveEnded を送る。
 * 閉じたら、その実行の summary と区間 API のキャッシュに古いものとしての印を付ける (閉じた後も記録は書き足されうるため。
 * 次にその実行を選ぶと、TanStack Query が読み直す)
 */
export const useLiveConnection = (): LiveConnection => {
  const { state, dispatch } = useViewState();
  const queryClient = useQueryClient();
  const run = state.run;
  const active = state.liveSession === "active";
  const loaded = [
    useRunSummary(run).data,
    useRangeItems(run, "requests").data,
    useRangeItems(run, "frames").data,
    useRangeItems(run, "pad").data,
    useRangeItems(run, "events").data,
  ].every((data) => data !== undefined);
  const [session, setSession] = useState<LiveConnection>(CONNECTING);
  // 接続を張り直さずに、足す間隔を今の幅で決めるため、幅は ref で渡す
  const widthRef = useRef(state.width);
  useEffect(() => {
    widthRef.current = state.width;
  }, [state.width]);

  useEffect(() => {
    if (run === null || !active || !loaded) {
      return;
    }
    const close = openLiveSession(queryClient, run, {
      onConnection: setSession,
      onEnded: (recordEnd, latestN) => dispatch({ type: "liveEnded", recordEnd, latestN }),
      flushIntervalMs: () => flushIntervalMs(widthRef.current),
    });
    return () => {
      close();
      markRunRecordsStale(queryClient, run);
      setSession(CONNECTING);
    };
  }, [run, active, loaded, queryClient, dispatch]);

  return useMemo(
    () => liveConnectionOf(state.liveSession, state.endReason, session),
    [state.liveSession, state.endReason, session],
  );
};
