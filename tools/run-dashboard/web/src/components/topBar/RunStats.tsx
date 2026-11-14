// 上部のバーの、選んでいる実行の値 (再生位置・要求の数・判断の所要時間の中央値・advisor の状態・ゲームの情報の
// 最後の書き換え)。
import { advisorChipOf } from "../../advisor/advisorChip.ts";
import { advisorStatusAt } from "../../advisor/advisorStatus.ts";
import type { NoteKind } from "../../api/apiTypes.ts";
import { formatSeconds } from "../../format/formatValues.ts";
import { lastRewriteAtOrBefore } from "../../notes/noteHistory.ts";
import { rewriteChipOf } from "../../notes/rewriteChip.ts";
import type { RunView } from "../../state/useRunView.ts";
import { useViewState } from "../../state/ViewStateContext.tsx";
import { Chip } from "../common/Chip.tsx";
import styles from "./TopBar.module.css";

export type RunStatsProps = {
  view: RunView;
};

/** 再生位置と実行の長さ、要求の数、判断の中央値、advisor の 4 役割の chip、最後の書き換えの chip */
export const RunStats = ({ view }: RunStatsProps) => {
  const { dispatch } = useViewState();
  const { summary, position } = view;
  return (
    <>
      <span className={styles.position}>
        t = {formatSeconds(position)} / {formatSeconds(summary.t_end)} 秒
      </span>
      <div className={styles.stats}>
        <span className={styles.stat}>
          要求{" "}
          <span className={styles.number}>{summary.counts.requests.toLocaleString("ja-JP")}</span>{" "}
          回
        </span>
        <span className={styles.stat}>
          判断 中央値{" "}
          <span className={styles.number}>{formatSeconds(summary.decide_s_median)}</span> 秒
        </span>
        {summary.old_format ? (
          <span className={styles.stat}>advisor・ゲームの情報: 形が古いため表示しない</span>
        ) : (
          <>
            <AdvisorChips
              view={view}
              onOpen={(name) => dispatch({ type: "selectAdvisor", name })}
            />
            <RewriteChip
              view={view}
              onOpen={(kind, version) =>
                dispatch({ type: "selectFile", file: { type: "note", kind, version } })
              }
            />
          </>
        )}
      </div>
    </>
  );
};

type AdvisorChipsProps = {
  view: RunView;
  /** chip を押したときに、advisor の実行の名前で呼ぶ */
  onOpen: (name: string) => void;
};

/** 役割ごとの advisor の状態の chip。実行の無い役割は chip にせず、文言だけを出す */
const AdvisorChips = ({ view, onOpen }: AdvisorChipsProps) => (
  <div className={styles.chips}>
    <span className={styles.chipsLabel}>advisor</span>
    {advisorStatusAt(view.summary.advisor_runs, view.position)
      .map(advisorChipOf)
      .map((chip) => {
        const openName = chip.openName;
        if (openName === null) {
          return (
            <span key={chip.role} className={styles.plainText}>
              {chip.text}
            </span>
          );
        }
        return (
          <Chip
            key={chip.role}
            tone={chip.tone}
            title={`${openName} を advisor のタブで開く`}
            onClick={() => onOpen(openName)}
          >
            {chip.text}
          </Chip>
        );
      })}
  </div>
);

type RewriteChipProps = {
  view: RunView;
  /** chip を押したときに、開くファイルと版で呼ぶ */
  onOpen: (kind: NoteKind, version: number) => void;
};

/**
 * 再生位置までのゲームの情報の最後の書き換えの chip。external を含めば external の見た目。
 * 書き換えが無ければ chip にせず「起動時のまま」の文言だけを出す
 */
const RewriteChip = ({ view, onOpen }: RewriteChipProps) => {
  const chip = rewriteChipOf(lastRewriteAtOrBefore(view.summary.note_versions, view.position));
  const open = chip.open;
  return (
    <div className={styles.chips}>
      <span className={styles.chipsLabel}>ゲームの情報</span>
      {open === null ? (
        <span className={styles.plainText}>{chip.text}</span>
      ) : (
        <Chip
          tone={chip.external ? "external" : "default"}
          title="この版をファイルのタブで開く"
          onClick={() => onOpen(open.kind, open.version)}
        >
          {chip.text}
        </Chip>
      )}
    </div>
  );
};
