// 上の段。映像・コントローラー入力・選んでいる要求の 3 つの区画を横に並べる。
import { useRunView } from "../../state/useRunView.ts";
import { ControllerPanel } from "./ControllerPanel.tsx";
import { SelectedRequestPanel } from "./SelectedRequestPanel.tsx";
import styles from "./TopRow.module.css";
import { VideoPanel } from "./VideoPanel.tsx";

/** 選んでいる実行を読み込み中は、各区画が骨組みを出す */
export const TopRow = () => {
  const runView = useRunView();
  const view = runView.status === "ready" ? runView.view : null;
  return (
    <div className={styles.row}>
      <VideoPanel view={view} className={styles.video} />
      <ControllerPanel view={view} className={styles.controller} />
      <SelectedRequestPanel view={view} className={styles.request} />
    </div>
  );
};
