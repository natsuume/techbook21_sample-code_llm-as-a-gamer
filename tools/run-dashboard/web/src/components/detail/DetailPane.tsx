// 詳細ペインの区画。要求・フレーム・advisor・ファイル・メモのタブと、選んでいるタブの中身を出す。
// 広い画面では、タブの中身 (tabpanel) だけが縦にスクロールする。
import { useLayoutEffect, useRef } from "react";
import { fileSelectionKey } from "../../detail/noteFiles.ts";
import { type RunView, useRunView } from "../../state/useRunView.ts";
import { useViewState } from "../../state/ViewStateContext.tsx";
import type { DetailTab, ViewState } from "../../state/viewState.ts";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import { type TabItem, Tabs } from "../common/Tabs.tsx";
import { AdvisorTab } from "./AdvisorTab.tsx";
import styles from "./DetailPane.module.css";
import { DetailSkeleton } from "./DetailSkeleton.tsx";
import { FileTab } from "./FileTab.tsx";
import { FrameTab } from "./FrameTab.tsx";
import { MemoTab } from "./MemoTab.tsx";
import { RequestTab } from "./RequestTab.tsx";

/** タブの順と文言 */
const TAB_ITEMS: readonly TabItem<DetailTab>[] = [
  { id: "request", label: "要求" },
  { id: "frame", label: "フレーム" },
  { id: "advisor", label: "advisor" },
  { id: "file", label: "ファイル" },
  { id: "memo", label: "メモ" },
];

/**
 * タブの中身の先頭に戻すかを決める文字列。タブと、タブで開いているもの (要求・フレーム・advisor の実行・ファイル) が
 * 変わったら変わる (問いの件数の切り替えや差分と全文の切り替えでは変わらない)
 */
const contentKeyOf = (state: ViewState): string =>
  [
    state.tab,
    state.selectedN,
    state.frameIndex,
    state.advisorName,
    fileSelectionKey(state.file),
  ].join("|");

export type DetailPaneProps = {
  /** 枠に足す class (画面の配置で決める大きさ) */
  className?: string;
};

/** 詳細ペイン (aside)。選んでいる実行を読み込み中は、タブの中身の代わりに骨組みを出す */
export const DetailPane = ({ className }: DetailPaneProps) => {
  const runView = useRunView();
  const { state, dispatch } = useViewState();
  const contentRef = useRef<HTMLDivElement>(null);
  const lastContentKey = useRef<string | null>(null);
  const contentKey = contentKeyOf(state);

  // 開くものが変わったら、本文のスクロールを先頭に戻す (目的の要素へのスクロールは、各タブが後から useEffect で行う)
  useLayoutEffect(() => {
    if (lastContentKey.current === contentKey) {
      return;
    }
    lastContentKey.current = contentKey;
    const panel = contentRef.current?.closest('[role="tabpanel"]');
    if (panel instanceof HTMLElement) {
      panel.scrollTop = 0;
    }
  }, [contentKey]);

  const view = runView.status === "ready" ? runView.view : null;
  const tab = state.tab;
  const loading = view === null && tab !== "memo";
  return (
    <aside aria-label="詳細" className={joinClassNames(styles.pane, className)}>
      <Tabs
        label="詳細の種類"
        items={TAB_ITEMS}
        selected={tab}
        onSelect={(next) => dispatch({ type: "setTab", tab: next })}
        className={styles.tabs}
        panelClassName={styles.body}
      >
        <div ref={contentRef} className={styles.content} aria-busy={loading || undefined}>
          {tab === "memo" ? <MemoTab /> : null}
          {tab !== "memo" && view === null ? <DetailSkeleton tab={tab} /> : null}
          {tab !== "memo" && view !== null ? <TabContent view={view} tab={tab} /> : null}
        </div>
      </Tabs>
    </aside>
  );
};

/** 選んでいるタブ (メモ以外) の中身 */
const TabContent = ({ view, tab }: { view: RunView; tab: Exclude<DetailTab, "memo"> }) => {
  switch (tab) {
    case "request":
      return <RequestTab view={view} />;
    case "frame":
      return <FrameTab view={view} />;
    case "advisor":
      return <AdvisorTab view={view} />;
    case "file":
      return <FileTab view={view} />;
  }
};
