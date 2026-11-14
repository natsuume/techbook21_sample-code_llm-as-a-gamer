// ライブの接続の状態の文言、「ライブ」と「最新に戻る」を押せない理由、終わった実行をライブで開き直すかの判定を
// 決める純粋な関数。
import type { DisplayMode, LiveEndReason, LiveSession } from "../state/viewState.ts";

/**
 * サーバー送信イベント (/live) の接続の状態。
 * - idle: つないでいない (liveSession が active でない)
 * - connecting: 区間 API を読み終える前と、つないで最初のイベントが届く前
 * - open: イベントが届いている
 * - reconnecting: 切れて、区間 API を読み直してつなぎ直す間。failures は続けて失敗した回数、retryInMs は次に
 *   試すまでの待ち (ミリ秒)
 * - ended: end を受け取った。recordEnd は終了の記録があったか
 */
export type LiveConnection =
  | { state: "idle" }
  | { state: "connecting" }
  | { state: "open" }
  | { state: "reconnecting"; failures: number; retryInMs: number }
  | { state: "ended"; recordEnd: boolean };

/** 上部のバーに出す接続の状態の文言と、「ライブ」「最新に戻る」を押せない理由の文言 */
export const LIVE_STATUS_TEXTS = {
  connecting: "接続中",
  following: "ライブ · 最新を表示中",
  paused: "一時停止中 · 記録は受け取り続けている",
  replaying: "実行中 · 記録を足し続けている (再生位置は動かさない)",
  reconnecting: "接続が切れた · 読み直してつなぎ直している",
  recordEnded: "実行が終わった",
  stalled: "記録が 10 秒止まったため、終わった実行として表示 (再開したらライブに戻す)",
  liveNotInProgress:
    "実行中でないため、ライブで見られない (終了の記録があるか、記録が 10 秒以上止まっている)",
  liveRecordEnded: "実行が終わったため、ライブで見られない",
  liveStalled: "記録が 10 秒止まったため、ライブで見られない (再開したら自動でライブに戻す)",
  resumeInReplay: "ライブの表示のときに使う",
  resumeFollowing: "最新を表示している",
} as const;

/**
 * 上部のバーに出す接続の状態の文言を返す。display と follow は ViewState の値。
 * - idle: "" (場所は確保したまま空にする)
 * - connecting: connecting
 * - open: display が live で follow なら following、live で follow でなければ paused、replay なら replaying
 * - reconnecting: failures が 0 なら reconnecting。1 以上なら
 *   "接続が切れた · <秒> 秒後につなぎ直す (<failures> 回失敗)" (秒は retryInMs / 1000 を切り上げた整数)
 * - ended: recordEnd なら recordEnded、そうでなければ stalled
 */
export const liveStatusText = (
  connection: LiveConnection,
  display: DisplayMode,
  follow: boolean,
): string => {
  switch (connection.state) {
    case "idle":
      return "";
    case "connecting":
      return LIVE_STATUS_TEXTS.connecting;
    case "open":
      if (display === "replay") {
        return LIVE_STATUS_TEXTS.replaying;
      }
      return follow ? LIVE_STATUS_TEXTS.following : LIVE_STATUS_TEXTS.paused;
    case "reconnecting":
      return connection.failures === 0
        ? LIVE_STATUS_TEXTS.reconnecting
        : `接続が切れた · ${Math.ceil(connection.retryInMs / 1000)} 秒後につなぎ直す (${connection.failures} 回失敗)`;
    case "ended":
      return connection.recordEnd ? LIVE_STATUS_TEXTS.recordEnded : LIVE_STATUS_TEXTS.stalled;
  }
};

/**
 * 表示の切り替えの「ライブ」を押せない理由を返す。押せれば null。
 * - liveSession が active: null
 * - none: liveNotInProgress
 * - ended: endReason が recordEnd なら liveRecordEnded、stalled なら liveStalled
 */
export const liveToggleReason = (
  liveSession: LiveSession,
  endReason: LiveEndReason | null,
): string | null => {
  switch (liveSession) {
    case "active":
      return null;
    case "none":
      return LIVE_STATUS_TEXTS.liveNotInProgress;
    case "ended":
      return endReason === "recordEnd"
        ? LIVE_STATUS_TEXTS.liveRecordEnded
        : LIVE_STATUS_TEXTS.liveStalled;
  }
};

/**
 * 「最新に戻る」を押せない理由を返す。押せれば null。
 * - display が replay: resumeInReplay
 * - live で follow (最新を追っている): resumeFollowing
 * - live で follow でない (一時停止中): null
 */
export const resumeButtonReason = (display: DisplayMode, follow: boolean): string | null => {
  if (display === "replay") {
    return LIVE_STATUS_TEXTS.resumeInReplay;
  }
  return follow ? LIVE_STATUS_TEXTS.resumeFollowing : null;
};

/** 実行の一覧の 1 件の、選んでいる実行の in_progress と、その一覧を読み終えた時刻 (query の dataUpdatedAt) */
export type ListedRun = { inProgress: boolean; readAt: number };

/**
 * 終わった実行 (liveSession が none か ended) を、区間 API を読み直してライブで開き直すかを返す。
 * inactiveSince は liveSession が none・ended になった時刻。
 * - liveSession が active なら false
 * - listed か inactiveSince が null なら false
 * - それ以外は listed.inProgress && listed.readAt > inactiveSince
 * 一覧の値の変わり目 (false → true) は見ない。記録が 10 秒止まって end を受け取った直後に再開すると、一覧の値は
 * true のまま変わらないことがあるため、「終わった後に読み終えた一覧が実行中と言うか」で決める
 */
export const shouldReopenLive = (
  listed: ListedRun | null,
  liveSession: LiveSession,
  inactiveSince: number | null,
): boolean => {
  if (liveSession === "active" || listed === null || inactiveSince === null) {
    return false;
  }
  return listed.inProgress && listed.readAt > inactiveSince;
};
