// 上の段のコントローラー入力。pad の記録があればそれを、無ければ要求の決めた操作から組み立てた入力を図と文で出し、
// どちらを使ったかを見出しの横に出す。
import { controllerAtPlayback } from "../../controller/playbackController.ts";
import type { RunView } from "../../state/useRunView.ts";
import { EmptyState } from "../common/EmptyState.tsx";
import { Panel } from "../common/Panel.tsx";
import { Skeleton } from "../common/Skeleton.tsx";
import { ControllerFigure } from "./ControllerFigure.tsx";
import styles from "./ControllerPanel.module.css";

export type ControllerPanelProps = {
  /** 選んでいる実行の値。読み込み中は null (骨組みを出す) */
  view: RunView | null;
  className?: string;
};

/** 入力の図の区画。pad と要求のどちらの記録も無い実行では「記録なし」 */
export const ControllerPanel = ({ view, className }: ControllerPanelProps) => {
  if (view === null) {
    return (
      <Panel title="コントローラー入力" busy className={className}>
        <div className={styles.skeleton}>
          <Skeleton height="auto" className={styles.figureSkeleton} />
          <Skeleton width="80%" height="1rem" />
        </div>
      </Panel>
    );
  }
  const snapshot = controllerAtPlayback({
    pad: view.pad,
    requests: view.requests,
    neutralTimes: view.neutralTimes,
    position: view.position,
    nextRequestPosition: view.nextRequestPosition,
  });
  if (snapshot === null) {
    return (
      <Panel title="コントローラー入力" className={className}>
        <EmptyState>記録なし</EmptyState>
      </Panel>
    );
  }
  return (
    <Panel
      title="コントローラー入力"
      meta={snapshot.source === "pad" ? "pad の記録から" : "要求の操作から組み立て"}
      className={className}
    >
      <ControllerFigure snapshot={snapshot} />
    </Panel>
  );
};
