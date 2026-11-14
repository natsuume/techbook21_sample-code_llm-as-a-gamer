// 画面の上部のバー。アプリ名・実行の選択と、選んでいる実行の値を横に並べる (幅が足りなければ折り返す)。

import { useRunView } from "../../state/useRunView.ts";
import { useViewState } from "../../state/ViewStateContext.tsx";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import { Skeleton } from "../common/Skeleton.tsx";
import { RunSelect } from "./RunSelect.tsx";
import { RunStats } from "./RunStats.tsx";
import styles from "./TopBar.module.css";

export type TopBarProps = {
  /** 枠に足す class (画面の配置で決める位置) */
  className?: string;
};

/**
 * アプリ名「試行分析ダッシュボード」と実行の select。実行を読めたら RunStats を、読み込み中は骨組みを出す
 * (読めなかったときの理由は画面の本文に出す)
 */
export const TopBar = ({ className }: TopBarProps) => {
  const { state, dispatch } = useViewState();
  const runView = useRunView();
  return (
    <header
      className={joinClassNames(styles.bar, className)}
      aria-busy={runView.status === "loading" ? true : undefined}
    >
      <h1 className={styles.appName}>試行分析ダッシュボード</h1>
      <RunSelect
        className={styles.runSelect}
        value={state.run}
        onSelect={(run) => dispatch({ type: "selectRun", run })}
      />
      {runView.status === "ready" ? <RunStats view={runView.view} /> : null}
      {runView.status === "loading" ? (
        <>
          <Skeleton width="9.5rem" height="1.25rem" />
          <Skeleton width="min(50rem, 100%)" height="var(--chip-height)" className={styles.stats} />
        </>
      ) : null}
    </header>
  );
};
