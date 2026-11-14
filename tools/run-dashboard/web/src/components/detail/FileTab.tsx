// 詳細ペインのファイルのタブ。ゲームの情報の 4 ファイル (文字数・版の数・最後の書き換え) と、advisor の実行ごとの
// 入出力のファイルの一覧を出し、その下の表示部に選んだファイルの中身を出す。
import { useMemo, useRef } from "react";
import { useAdvisorDetail, useNoteDetail } from "../../api/queries.ts";
import { toQueryView } from "../../api/queryView.ts";
import { type AdvisorRunCard, advisorRoleGroups } from "../../detail/advisorRuns.ts";
import { fileTabNotice } from "../../detail/detailNotices.ts";
import {
  fileSelectionKey,
  lastRewriteText,
  noteDirectoryPath,
  noteFileRows,
  resolveFileSelection,
} from "../../detail/noteFiles.ts";
import { fileNameOf } from "../../detail/requestFacts.ts";
import { formatBytes } from "../../format/formatValues.ts";
import { type NoteFileStats, noteFileStats } from "../../notes/noteHistory.ts";
import type { RunView } from "../../state/useRunView.ts";
import { useViewState } from "../../state/ViewStateContext.tsx";
import type { FileSelection } from "../../state/viewState.ts";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import { NO_RECORD_NOTICE } from "../../timeline/laneNotice.ts";
import { EmptyState } from "../common/EmptyState.tsx";
import { ErrorState } from "../common/ErrorState.tsx";
import { Skeleton } from "../common/Skeleton.tsx";
import { AdvisorFileViewer } from "./AdvisorFileViewer.tsx";
import content from "./DetailContent.module.css";
import styles from "./FileTab.module.css";
import { NoteFileViewer } from "./NoteFileViewer.tsx";
import { useScrollIntoView } from "./useScrollIntoView.ts";

/**
 * ファイルのタブ。古い形の実行では文言だけを出す。advisor の入出力のまとまりは、表示部で開いているファイルを含むものだけを
 * 開いておく (表示部より上の一覧の高さを、読み込みの後に変えないため)
 */
export const FileTab = ({ view }: { view: RunView }) => {
  const { state } = useViewState();
  const viewerRef = useRef<HTMLElement>(null);
  const noteVersions = view.summary.note_versions;
  const stats = useMemo(() => noteFileStats(noteVersions), [noteVersions]);
  const cards = useMemo(
    () =>
      advisorRoleGroups(view.summary.advisor_runs, view.rewrites).flatMap((group) => group.cards),
    [view.summary.advisor_runs, view.rewrites],
  );
  const selection = resolveFileSelection(state.file, stats);
  // 一覧や他のタブからファイルを選んだときだけ、表示部をスクロールして見せる (タブを開いただけでは一覧を見せる)
  useScrollIntoView(
    viewerRef,
    state.file === null || selection === null ? null : fileSelectionKey(selection),
    "start",
  );

  const notice = fileTabNotice(view.summary.old_format);
  if (notice !== null) {
    return (
      <>
        <h3 className={content.heading}>ファイル</h3>
        <EmptyState>{notice}</EmptyState>
      </>
    );
  }
  return (
    <>
      <h3 className={content.heading}>ファイル</h3>
      <section className={content.block}>
        <h4 className={content.subheading}>
          ゲームの情報 ({noteDirectoryPath(view.summary.game)})
        </h4>
        {noteVersions.length === 0 ? (
          <EmptyState>{NO_RECORD_NOTICE}</EmptyState>
        ) : (
          <NoteFileList view={view} stats={stats} selection={selection} />
        )}
      </section>
      <section className={content.block}>
        <h4 className={content.subheading}>
          advisor の入出力 ({cards.length.toLocaleString("ja-JP")} 回)
        </h4>
        {cards.length === 0 ? (
          <EmptyState>{NO_RECORD_NOTICE}</EmptyState>
        ) : (
          cards.map((card) => (
            <AdvisorFileGroup key={card.run.name} view={view} card={card} selection={selection} />
          ))
        )}
      </section>
      <section
        ref={viewerRef}
        className={joinClassNames(content.block, styles.viewer)}
        aria-label="ファイルの中身"
      >
        <h4 className={content.subheading}>中身</h4>
        {selection === null ? (
          <EmptyState>一覧からファイルを選ぶと、ここに中身を出す</EmptyState>
        ) : null}
        {selection?.type === "note" ? (
          <NoteFileViewer
            view={view}
            stats={stats}
            kind={selection.kind}
            version={selection.version}
          />
        ) : null}
        {selection?.type === "advisorFile" ? (
          <AdvisorFileViewer view={view} path={selection.path} />
        ) : null}
      </section>
    </>
  );
};

type NoteFileListProps = {
  view: RunView;
  stats: readonly NoteFileStats[];
  selection: FileSelection | null;
};

/**
 * ゲームの情報の 4 ファイルの一覧。文字数は表示部で開いている版 (advisor の入出力を開いているときは最後の版) の中身で
 * 数える。押すとそのファイルの最後の版を開く
 */
const NoteFileList = ({ view, stats, selection }: NoteFileListProps) => {
  const { dispatch } = useViewState();
  const latestVersion = Math.max(...view.summary.note_versions.map((item) => item.version));
  const countVersion = selection?.type === "note" ? selection.version : latestVersion;
  const noteView = toQueryView(useNoteDetail(view.run, countVersion));
  const files = noteView.state === "ready" ? noteView.data.files : undefined;
  const loading = noteView.state === "loading" || noteView.state === "idle";
  const rows = noteFileRows(stats, files);
  return (
    <>
      <p className={content.note}>文字数は版 {countVersion} の時点</p>
      <ul className={styles.fileList}>
        {rows.map((row) => {
          const lastVersion = stats.find((stat) => stat.kind === row.kind)?.versions.at(-1);
          const current = selection?.type === "note" && selection.kind === row.kind;
          return (
            <li key={row.kind}>
              <button
                type="button"
                className={styles.fileItem}
                aria-current={current ? "true" : undefined}
                onClick={() => {
                  if (lastVersion !== undefined) {
                    dispatch({
                      type: "selectFile",
                      file: { type: "note", kind: row.kind, version: lastVersion },
                    });
                  }
                }}
              >
                <span className={styles.fileName}>{row.fileName}</span>
                <span className={styles.fileSize}>
                  {loading ? (
                    <Skeleton width="3rem" height="0.875rem" />
                  ) : row.characters === null ? (
                    "—"
                  ) : (
                    `${row.characters.toLocaleString("ja-JP")} 字`
                  )}
                </span>
                <span className={styles.fileMeta}>
                  版 {row.versionCount.toLocaleString("ja-JP")} 個 ·{" "}
                  <span className={row.last?.external ? content.external : undefined}>
                    {lastRewriteText(row.last)}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {noteView.state === "error" ? (
        <ErrorState error={noteView.error} onRetry={noteView.retry} />
      ) : null}
    </>
  );
};

type AdvisorFileGroupProps = {
  view: RunView;
  card: AdvisorRunCard;
  selection: FileSelection | null;
};

/**
 * advisor の実行 1 回の入出力のファイルの一覧 (開閉できる)。表示部で開いているファイルを含むまとまりは開いておく。
 * ファイルは advisor の実行の API から読む
 */
const AdvisorFileGroup = ({ view, card, selection }: AdvisorFileGroupProps) => {
  const { dispatch } = useViewState();
  const name = card.run.name;
  const detailView = toQueryView(useAdvisorDetail(view.run, name));
  const containsSelection =
    selection?.type === "advisorFile" && selection.path.startsWith(`${name}/`);
  return (
    <details className={styles.group} open={containsSelection || undefined}>
      <summary className={styles.groupSummary}>
        <span className={styles.groupName}>{name}/</span>
        {card.running ? "(実行中)" : null}
      </summary>
      {detailView.state === "loading" || detailView.state === "idle" ? (
        <Skeleton height="2.5rem" />
      ) : null}
      {detailView.state === "error" ? (
        <ErrorState error={detailView.error} onRetry={detailView.retry} />
      ) : null}
      {detailView.state === "ready" && detailView.data.files.length === 0 ? (
        <EmptyState>ファイルなし</EmptyState>
      ) : null}
      {detailView.state === "ready" && detailView.data.files.length > 0 ? (
        <ul className={styles.fileList}>
          {detailView.data.files.map((file) => (
            <li key={file.path}>
              <button
                type="button"
                className={styles.fileItem}
                aria-current={
                  selection?.type === "advisorFile" && selection.path === file.path
                    ? "true"
                    : undefined
                }
                aria-label={`${file.path} を開く`}
                onClick={() =>
                  dispatch({ type: "selectFile", file: { type: "advisorFile", path: file.path } })
                }
              >
                <span className={styles.fileName}>{fileNameOf(file.path)}</span>
                <span className={styles.fileSize}>{formatBytes(file.bytes)}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </details>
  );
};
