// 上部のバーのライブの操作。表示の切り替え (ライブ / 終わった実行)、「最新に戻る」、接続の状態の文言を横に並べる。
// どれも、実行を選んでいる間はいつも同じ場所・同じ幅に置く (押せないときは理由を添えて押せない表示にする)。
import { useLiveConnectionState } from "../../live/LiveConnectionContext.tsx";
import { liveStatusText, liveToggleReason, resumeButtonReason } from "../../live/liveStatus.ts";
import { latestRequestN } from "../../state/runView.ts";
import { useRunView } from "../../state/useRunView.ts";
import { useViewState } from "../../state/ViewStateContext.tsx";
import type { DisplayMode } from "../../state/viewState.ts";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import { Button } from "../common/Button.tsx";
import { SegmentedControl } from "../common/SegmentedControl.tsx";
import styles from "./TopBar.module.css";

export type LiveControlsProps = {
  className?: string;
};

/**
 * 「表示」の切り替え (「ライブ」は liveToggleReason が null でなければ押せない)、「最新に戻る」
 * (resumeButtonReason が null でなければ押せない)、接続の状態の文言 (role="status")。
 * 切り替えの latestN は、表示中のデータの最後の要求 (読み込み中は null)
 */
export const LiveControls = ({ className }: LiveControlsProps) => {
  const { state, dispatch } = useViewState();
  const runView = useRunView();
  const connection = useLiveConnectionState();
  const latestN = runView.status === "ready" ? latestRequestN(runView.view) : null;
  const statusText = liveStatusText(connection, state.display, state.follow);
  return (
    <div className={joinClassNames(styles.live, className)}>
      <SegmentedControl<DisplayMode>
        label="表示"
        options={[
          {
            value: "live",
            label: "ライブ",
            disabledReason: liveToggleReason(state.liveSession, state.endReason),
          },
          { value: "replay", label: "終わった実行" },
        ]}
        value={state.display}
        onChange={(display) => dispatch({ type: "setDisplay", display, latestN })}
      />
      <Button
        disabledReason={resumeButtonReason(state.display, state.follow)}
        onClick={() => dispatch({ type: "resumeFollow" })}
      >
        最新に戻る
      </Button>
      <span
        role="status"
        aria-live="polite"
        className={styles.liveStatus}
        title={statusText === "" ? undefined : statusText}
      >
        {statusText}
      </span>
    </div>
  );
};
