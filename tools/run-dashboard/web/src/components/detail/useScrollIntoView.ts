// 詳細ペインのタブを開いたときと選ぶものが変わったときに、目的のカードや表示部をスクロールして見せるフック。
import { type RefObject, useEffect } from "react";

/**
 * key が null でない値に変わったとき (タブの中身を描いたときを含む) に、targetRef の要素を scrollIntoView する。
 * key は「何を見せるか」を表す文字列 (見せるものが無いか、まだ描けていなければ null)。同じ key のまま描き直しても
 * スクロールしない。ペインの本文を先頭に戻す処理 (DetailPane の useLayoutEffect) の後に動くよう、useEffect で行う
 */
export const useScrollIntoView = (
  targetRef: RefObject<HTMLElement | null>,
  key: string | null,
  block: ScrollLogicalPosition,
): void => {
  useEffect(() => {
    if (key === null) {
      return;
    }
    targetRef.current?.scrollIntoView({ block, inline: "nearest" });
  }, [targetRef, key, block]);
};
