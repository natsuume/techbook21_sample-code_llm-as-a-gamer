// 実行を選ぶ前に、上部のバーの下に出す案内。実行の一覧の読み込みの状態に応じて文言を変える。
import { useRuns } from "../../api/queries.ts";
import { toQueryView } from "../../api/queryView.ts";
import { EmptyState } from "../common/EmptyState.tsx";
import { ErrorState } from "../common/ErrorState.tsx";
import { Panel } from "../common/Panel.tsx";
import { Skeleton } from "../common/Skeleton.tsx";
import styles from "./RunGuide.module.css";

export type RunGuideProps = {
  /** 枠に足す class (画面の配置で決める位置) */
  className?: string;
};

/**
 * 実行の一覧が読めたら「実行を選んでください」(一覧が空なら「runs/ に実行がありません」)、読み込み中は骨組み、
 * 読めなければ理由と「読み直す」を出す
 */
export const RunGuide = ({ className }: RunGuideProps) => {
  const runs = toQueryView(useRuns());
  return (
    <Panel label="実行の案内" busy={runs.state === "loading"} className={className}>
      {runs.state === "ready" && runs.data.length > 0 ? (
        <p className={styles.message}>実行を選んでください</p>
      ) : null}
      {runs.state === "ready" && runs.data.length === 0 ? (
        <EmptyState>
          <code className={styles.code}>runs/</code> に実行がありません
        </EmptyState>
      ) : null}
      {runs.state === "loading" || runs.state === "idle" ? (
        <Skeleton width="12rem" height="1.3125rem" />
      ) : null}
      {runs.state === "error" ? <ErrorState error={runs.error} onRetry={runs.retry} /> : null}
    </Panel>
  );
};
