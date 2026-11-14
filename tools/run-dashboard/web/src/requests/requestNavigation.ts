// 選んでいる要求から再生位置を求め、前後の要求を選ぶための純粋な関数。
import type { RequestItem } from "../api/apiTypes.ts";

/** 要求の時刻 (再生位置に使う値) を返す。t_received ?? t_sent */
export const requestPosition = (request: Pick<RequestItem, "t_sent" | "t_received">): number =>
  request.t_received ?? request.t_sent;

/** 配列の最初の要求の n を返す。要求が無ければ null */
export const firstRequestN = (requests: readonly RequestItem[]): number | null =>
  requests[0]?.n ?? null;

/**
 * 番号 currentN の要求の、配列の順で前 (direction が -1) か次 (1) の要求の n を返す。
 * 失敗した要求も含めて数える。端にいるときと、currentN の要求が無いときは null
 */
export const adjacentRequestN = (
  requests: readonly RequestItem[],
  currentN: number,
  direction: -1 | 1,
): number | null => {
  const currentIndex = requests.findIndex((request) => request.n === currentN);
  if (currentIndex < 0) {
    return null;
  }
  return requests[currentIndex + direction]?.n ?? null;
};

/** 再生位置を返す。selected があれば requestPosition(selected)、無ければ tEnd ?? 0 */
export const playbackPosition = (selected: RequestItem | null, tEnd: number | null): number =>
  selected === null ? (tEnd ?? 0) : requestPosition(selected);
