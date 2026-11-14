// サーバー送信イベント (/live) の 1 件 (イベントの名前と data の文字列) を、画面で使う LiveEvent に読む純粋な関数。
import { LIVE_EVENT_KINDS, type LiveEvent, type LiveEventKind } from "../api/apiTypes.ts";

const isLiveEventKind = (kind: string): kind is LiveEventKind =>
  (LIVE_EVENT_KINDS as readonly string[]).includes(kind);

/** JSON.parse の結果。読めなければ undefined */
const parseJson = (text: string): unknown => {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
};

/**
 * イベントの名前 kind と data の文字列を LiveEvent にする。
 * - kind が LIVE_EVENT_KINDS に無ければ null
 * - data を JSON.parse できなければ null
 * - 読んだ値が object でない (null・配列・数・文字列・真偽値) なら null
 * - それ以外は { kind, data: 読んだ値 }。data の中身の型は検査しない (fetchJson と同じく、サーバーの応答の形を信じる)
 */
export const parseLiveEvent = (kind: string, data: string): LiveEvent | null => {
  if (!isLiveEventKind(kind)) {
    return null;
  }
  const value = parseJson(data);
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  // data の形はサーバーの契約 (kind ごとの LiveEventDataOf) を信じる
  return { kind, data: value } as LiveEvent;
};
