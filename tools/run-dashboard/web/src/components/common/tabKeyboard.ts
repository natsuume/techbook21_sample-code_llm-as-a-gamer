// タブの列 (tablist) のキーボード操作で、次に選ぶタブの位置を求める純粋な関数。

/**
 * 押したキーから、次に選ぶタブの位置を返す。count はタブの数、current は今のタブの位置。
 * - ArrowRight: 次 (最後なら最初)、ArrowLeft: 前 (最初なら最後)、Home: 最初、End: 最後
 * - それ以外のキーと、タブが無いときは null (タブの操作ではない)
 */
export const nextTabIndex = (key: string, current: number, count: number): number | null => {
  if (count <= 0) {
    return null;
  }
  switch (key) {
    case "ArrowRight":
      return (current + 1) % count;
    case "ArrowLeft":
      return (current - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
};
