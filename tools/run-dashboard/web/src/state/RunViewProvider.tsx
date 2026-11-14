// 選んでいる実行の画面全体で使う値を 1 回だけ作り、子のコンポーネントに配る。ViewStateProvider の中に置く。
import type { ReactNode } from "react";
import { RunViewContext, useRunViewSource } from "./useRunView.ts";

/** useRunViewSource で作った RunViewState を、useRunView で読めるように配る */
export const RunViewProvider = ({ children }: { children: ReactNode }) => {
  const value = useRunViewSource();
  return <RunViewContext value={value}>{children}</RunViewContext>;
};
