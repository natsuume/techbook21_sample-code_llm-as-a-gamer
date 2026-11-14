// 選んでいる実行の画面全体で使う値とライブの接続の状態を 1 回だけ作り、子のコンポーネントに配る。
// ViewStateProvider の中に置く。終わった実行が実行中に戻ったときにライブで開き直すのもここで行う。
import type { ReactNode } from "react";
import { LiveConnectionContext } from "../live/LiveConnectionContext.tsx";
import { useLiveConnection } from "../live/useLiveConnection.ts";
import { useLiveReopen } from "../live/useLiveReopen.ts";
import { RunViewContext, useRunViewSource } from "./useRunView.ts";

/**
 * useRunViewSource で作った RunViewState を useRunView で、useLiveConnection で作った接続の状態を
 * useLiveConnectionState で読めるように配る
 */
export const RunViewProvider = ({ children }: { children: ReactNode }) => {
  const value = useRunViewSource();
  const connection = useLiveConnection();
  useLiveReopen();
  return (
    <RunViewContext value={value}>
      <LiveConnectionContext value={connection}>{children}</LiveConnectionContext>
    </RunViewContext>
  );
};
