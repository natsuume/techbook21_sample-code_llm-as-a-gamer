// 詳細ペインの要求のタブの見出しの下に出す文 (時刻) と、advisor の入出力のファイルの名前と種類を求める純粋な関数。
import type { RequestItem } from "../api/apiTypes.ts";
import { formatSeconds } from "../format/formatValues.ts";

/**
 * 要求の時刻の文 "送信 t=<送信> → 受信 t=<受信> · <result>"。受信が無ければ "送信 t=<送信> → 受信なし · <result>"
 */
export const requestTimeText = (
  request: Pick<RequestItem, "t_sent" | "t_received" | "result">,
): string => {
  const received =
    request.t_received === null ? "受信なし" : `受信 t=${formatSeconds(request.t_received)}`;
  return `送信 t=${formatSeconds(request.t_sent)} → ${received} · ${request.result}`;
};

/** 画像として表示するファイルの拡張子 */
const IMAGE_EXTENSION = /\.(jpe?g|png|gif|webp)$/i;

/** パスが画像のファイル (拡張子が jpg・jpeg・png・gif・webp) なら true */
export const isImagePath = (path: string): boolean => IMAGE_EXTENSION.test(path);

/** パスの最後の部分 (ファイルの名前) を返す */
export const fileNameOf = (path: string): string => path.split("/").at(-1) ?? path;
