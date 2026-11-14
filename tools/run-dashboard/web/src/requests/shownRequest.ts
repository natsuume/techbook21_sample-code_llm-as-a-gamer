// 要求の詳細を出す区画で、見出しの番号・決めた操作などに使う要求を決める純粋な関数。新しい要求の詳細を読む間は
// 前の要求の詳細を出し続けるため、区画の中身をすべてその前の要求のものにそろえる。
import type { RequestItem } from "../api/apiTypes.ts";

/**
 * 出す要求を返す。detail (要求の詳細。要求の項目を含む) が request と違う番号の要求のもの (新しい要求の詳細が
 * 届くまで残した前の要求の詳細) なら detail、それ以外 (detail が無いか、同じ番号) は request
 */
export const requestShownWithDetail = (
  request: RequestItem,
  detail: RequestItem | null,
): RequestItem => (detail !== null && detail.n !== request.n ? detail : request);
