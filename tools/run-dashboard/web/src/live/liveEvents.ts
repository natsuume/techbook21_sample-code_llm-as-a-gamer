// サーバー送信イベント (/live) の 1 件 (イベントの名前と data の文字列) を、画面で使う LiveEvent に読む純粋な関数。
import type { LiveEvent } from "../api/apiTypes.ts";

/**
 * イベントの名前 kind と data の文字列を LiveEvent にする。
 * - kind が LIVE_EVENT_KINDS に無ければ null
 * - data を JSON.parse できなければ null
 * - 読んだ値が object でない (null・配列・数・文字列・真偽値) なら null
 * - それ以外は { kind, data: 読んだ値 }。data の中身の型は検査しない (fetchJson と同じく、サーバーの応答の形を信じる)
 */
export const parseLiveEvent = (_kind: string, _data: string): LiveEvent | null => {
  throw new Error("not implemented");
};
