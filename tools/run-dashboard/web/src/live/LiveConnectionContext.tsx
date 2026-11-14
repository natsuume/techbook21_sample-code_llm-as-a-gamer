// ライブの接続の状態 (useLiveConnection が作る LiveConnection) を画面全体に配る context。
import { createContext, use } from "react";
import type { LiveConnection } from "./liveStatus.ts";

/** RunViewProvider が配る接続の状態 */
export const LiveConnectionContext = createContext<LiveConnection | null>(null);

/** ライブの接続の状態を返す。RunViewProvider の外で呼ぶと例外を投げる */
export const useLiveConnectionState = (): LiveConnection => {
  const value = use(LiveConnectionContext);
  if (value === null) {
    throw new Error("useLiveConnectionState は RunViewProvider の中で使う");
  }
  return value;
};
