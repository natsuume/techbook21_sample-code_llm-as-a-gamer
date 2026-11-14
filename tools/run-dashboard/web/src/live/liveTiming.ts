// ライブの時間の決まり (届いた記録をまとめて足す間隔・実行の一覧を読み直す間隔・つなぎ直すまでの待ち) と、
// 次に足す・つなぎ直すまでの待ちを求める純粋な関数。時刻はミリ秒。

/** 届いた記録をキャッシュに足す最小の間隔 (ミリ秒)。画面の更新を 1 秒に 4 回までにまとめる */
export const LIVE_FLUSH_INTERVAL_MS = 250;
/** 実行の一覧を読み直す間隔 (ミリ秒) */
export const RUN_LIST_REFRESH_MS = 5000;
/** つなぎ直すまでの待ちの上限 (ミリ秒) */
export const RECONNECT_MAX_MS = 10000;

/**
 * 次に記録を足すまでの待ち (ミリ秒) を返す。lastFlushAt は前に足した時刻 (まだ足していなければ null)、
 * now は今の時刻。
 * - lastFlushAt が null なら 0
 * - それ以外は max(0, lastFlushAt + LIVE_FLUSH_INTERVAL_MS - now)
 */
export const nextFlushDelayMs = (_lastFlushAt: number | null, _now: number): number => {
  throw new Error("not implemented");
};

/**
 * 区間 API を読み直してつなぎ直すまでの待ち (ミリ秒) を返す。failures は、接続が切れてから続けて失敗した
 * 読み直し・つなぎ直しの回数。
 * - 0 なら 0 (切れたらすぐ読み直す)
 * - 1 以上は min(1000 × 2^(failures - 1), RECONNECT_MAX_MS)
 */
export const reconnectDelayMs = (_failures: number): number => {
  throw new Error("not implemented");
};
