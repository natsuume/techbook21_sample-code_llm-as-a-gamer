// 試行分析ダッシュボードの画面全体の配置。上部のバーの下に、実行を選ぶ前は案内を、選んだ後は上の段・タイムライン・
// 詳細ペインを置く。
import styles from "./App.module.css";
import { ErrorState } from "./components/common/ErrorState.tsx";
import { Panel } from "./components/common/Panel.tsx";
import { DetailPane } from "./components/detail/DetailPane.tsx";
import { RunGuide } from "./components/guide/RunGuide.tsx";
import { Timeline } from "./components/timeline/Timeline.tsx";
import { TopBar } from "./components/topBar/TopBar.tsx";
import { TopRow } from "./components/topRow/TopRow.tsx";
import { useRunView } from "./state/useRunView.ts";

const App = () => {
  const runView = useRunView();
  return (
    <div className={styles.app}>
      <TopBar className={styles.topBar} />
      {runView.status === "noRun" ? <RunGuide className={styles.notice} /> : null}
      {runView.status === "error" ? (
        <Panel label="実行の読み込み" className={styles.notice}>
          <ErrorState error={runView.error} onRetry={runView.retry} />
        </Panel>
      ) : null}
      {runView.status === "loading" || runView.status === "ready" ? (
        <>
          <main className={styles.main}>
            <TopRow />
            <Timeline className={styles.timeline} />
          </main>
          <DetailPane className={styles.detail} />
        </>
      ) : null}
    </div>
  );
};

export default App;
