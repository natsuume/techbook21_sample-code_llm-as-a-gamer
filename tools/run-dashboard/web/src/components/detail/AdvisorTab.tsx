// 詳細ペインの advisor のタブ。advisor の実行を役割ごとにまとめてカードで出し、最後に advisor 以外の書き換えを出す。
// 選んでいる実行 (ViewState の advisorName) のカードは accent の枠にし、タブを開いたときにスクロールして見せる。
import { useMemo, useRef } from "react";
import { advisorRoleGroups, externalRewriteButtonText } from "../../detail/advisorRuns.ts";
import { advisorTabNotice } from "../../detail/detailNotices.ts";
import { externalRewrites } from "../../notes/noteHistory.ts";
import type { RunView } from "../../state/useRunView.ts";
import { useViewState } from "../../state/ViewStateContext.tsx";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import { Button } from "../common/Button.tsx";
import { EmptyState } from "../common/EmptyState.tsx";
import { AdvisorRunCard } from "./AdvisorRunCard.tsx";
import styles from "./AdvisorTab.module.css";
import content from "./DetailContent.module.css";
import { useScrollIntoView } from "./useScrollIntoView.ts";

/** advisor のタブ。古い形の実行と、advisor の実行も advisor 以外の書き換えも無い実行では、文言だけを出す */
export const AdvisorTab = ({ view }: { view: RunView }) => {
  const { state, dispatch } = useViewState();
  const selectedCardRef = useRef<HTMLElement>(null);
  const runs = view.summary.advisor_runs;
  const groups = useMemo(() => advisorRoleGroups(runs, view.rewrites), [runs, view.rewrites]);
  const external = useMemo(() => externalRewrites(view.rewrites), [view.rewrites]);
  const selectedName = runs.some((run) => run.name === state.advisorName)
    ? state.advisorName
    : null;
  useScrollIntoView(selectedCardRef, selectedName, "start");

  const notice = advisorTabNotice(view.summary.old_format, runs.length, external.length);
  if (notice !== null) {
    return (
      <>
        <h3 className={content.heading}>advisor</h3>
        <EmptyState>{notice}</EmptyState>
      </>
    );
  }
  return (
    <>
      <div className={content.headRow}>
        <h3 className={content.heading}>advisor</h3>
        <span className={content.headMeta}>実行 {runs.length.toLocaleString("ja-JP")} 回</span>
      </div>
      {groups.map((group) => (
        <section key={group.role} className={content.block} aria-label={`${group.role} の advisor`}>
          <h4 className={content.subheading}>
            {group.role} ({group.cards.length.toLocaleString("ja-JP")} 回)
          </h4>
          {group.cards.length === 0 ? (
            <EmptyState>実行なし</EmptyState>
          ) : (
            group.cards.map((card) => (
              <AdvisorRunCard
                key={card.run.name}
                view={view}
                card={card}
                selected={card.run.name === selectedName}
                cardRef={card.run.name === selectedName ? selectedCardRef : undefined}
              />
            ))
          )}
        </section>
      ))}
      <section className={joinClassNames(content.card, styles.externalCard)}>
        <h4 className={joinClassNames(content.cardName, content.external)}>
          advisor 以外の書き換え ({external.length.toLocaleString("ja-JP")} 回)
        </h4>
        {external.length === 0 ? (
          <EmptyState>なし</EmptyState>
        ) : (
          <div className={content.buttonRow}>
            {external.map((rewrite) => (
              <Button
                key={`${rewrite.version}-${rewrite.kind}`}
                className={content.wrapButton}
                onClick={() =>
                  dispatch({
                    type: "selectFile",
                    file: { type: "note", kind: rewrite.kind, version: rewrite.version },
                  })
                }
              >
                {externalRewriteButtonText(rewrite)}
              </Button>
            ))}
          </div>
        )}
      </section>
    </>
  );
};
