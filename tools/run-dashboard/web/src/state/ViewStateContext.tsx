// 画面の選択の状態 (ViewState) を useReducer で持ち、画面全体に配る context。
import { createContext, type Dispatch, type ReactNode, use, useMemo, useReducer } from "react";
import { initialViewState, type ViewAction, type ViewState, viewReducer } from "./viewState.ts";

/** context で配る値。state は今の選択の状態、dispatch は ViewAction を送って状態を変える関数 */
export type ViewStateContextValue = { state: ViewState; dispatch: Dispatch<ViewAction> };

const ViewStateContext = createContext<ViewStateContextValue | null>(null);

/** 子のコンポーネントに選択の状態を配る。状態は initialViewState から始まり、viewReducer で変わる */
export const ViewStateProvider = ({ children }: { children: ReactNode }) => {
  const [state, dispatch] = useReducer(viewReducer, initialViewState);
  const value = useMemo(() => ({ state, dispatch }), [state]);
  return <ViewStateContext value={value}>{children}</ViewStateContext>;
};

/** 選択の状態と dispatch を返す。ViewStateProvider の外で呼ぶと例外を投げる */
export const useViewState = (): ViewStateContextValue => {
  const value = use(ViewStateContext);
  if (value === null) {
    throw new Error("useViewState は ViewStateProvider の中で使う");
  }
  return value;
};
