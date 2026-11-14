// 棒や印の説明の文を、同じオブジェクトについて 1 回だけ作って覚える純粋な関数。
// 数の書式 (toLocaleString) は 1 回ごとの時間が大きいため、再生位置を動かすたびに数百の文を作り直さない。

/**
 * describe の結果を、渡したオブジェクトごとに WeakMap に覚えて返す関数を作る。
 * 同じオブジェクトには 2 回目から覚えた文を返す (呼び出し側は、一度渡したオブジェクトの中身を変えない)
 */
export const cachedDescriber = <T extends object>(
  describe: (item: T) => string,
): ((item: T) => string) => {
  const cache = new WeakMap<T, string>();
  return (item) => {
    const cached = cache.get(item);
    if (cached !== undefined) {
      return cached;
    }
    const text = describe(item);
    cache.set(item, text);
    return text;
  };
};
