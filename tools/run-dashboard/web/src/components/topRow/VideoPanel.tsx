// 上の段の映像。再生位置以前の最後のフレームの縮小画像と、その経過秒を出す。押すとフレームのタブで開く。
import { useState } from "react";
import { filePath } from "../../api/apiPaths.ts";
import { formatSeconds } from "../../format/formatValues.ts";
import type { RunView } from "../../state/useRunView.ts";
import { useViewState } from "../../state/ViewStateContext.tsx";
import { lastIndexAtOrBefore } from "../../timeline/rangeSearch.ts";
import { Panel } from "../common/Panel.tsx";
import { Skeleton } from "../common/Skeleton.tsx";
import styles from "./VideoPanel.module.css";

export type VideoPanelProps = {
  /** 選んでいる実行の値。読み込み中は null (骨組みを出す) */
  view: RunView | null;
  className?: string;
};

/**
 * 縮小画像の区画。frames の記録が無い実行と、再生位置以前のフレームが無いときは「記録なし」。
 * 画像を読めなければ「画像なし」
 */
export const VideoPanel = ({ view, className }: VideoPanelProps) => {
  const { dispatch } = useViewState();
  if (view === null) {
    return (
      <Panel label="映像" busy className={className}>
        <Skeleton height="auto" className={styles.skeleton} />
      </Panel>
    );
  }
  const index = view.frames.available ? lastIndexAtOrBefore(view.frameTimes, view.position) : -1;
  const frame = view.timedFrames[index];
  if (frame === undefined) {
    return (
      <Panel label="映像" className={className}>
        <div className={styles.screen}>
          <span className={styles.empty}>記録なし</span>
        </div>
      </Panel>
    );
  }
  const time = formatSeconds(frame.t);
  const src = filePath(view.run, frame.thumb);
  return (
    <Panel label="映像" className={className}>
      <button
        type="button"
        className={styles.screen}
        aria-label={`t=${time} 秒のフレームをフレームのタブで開く`}
        onClick={() => dispatch({ type: "selectFrame", index })}
      >
        <FrameImage key={src} src={src} />
        <span className={styles.time} aria-hidden="true">
          t={time}
        </span>
      </button>
    </Panel>
  );
};

/** フレームの縮小画像。読めなければ「画像なし」に替える (src が変わったら key で作り直す) */
const FrameImage = ({ src }: { src: string }) => {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return <span className={styles.empty}>画像なし</span>;
  }
  return <img className={styles.image} src={src} alt="" onError={() => setFailed(true)} />;
};
