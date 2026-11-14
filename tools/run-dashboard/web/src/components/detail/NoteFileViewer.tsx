// ファイルのタブの表示部の、ゲームの情報の 1 ファイル。版のボタンの列と「差分 / 全文」の切り替えを置き、選んだ版の中身を
// 1 つ前の版 (version - 1) との行の差分か全文で出す。中身は pre のテキストとして出す (HTML として解釈しない)。
import type { NoteKind } from "../../api/apiTypes.ts";
import { useNoteDetail } from "../../api/queries.ts";
import { type QueryView, toQueryView } from "../../api/queryView.ts";
import {
  noteContentView,
  noteFilePath,
  noteVersionLabel,
  noteVersionOptions,
  noteVersionText,
  type PreviousNoteText,
  previousNoteVersion,
} from "../../detail/noteFiles.ts";
import { countCharacters } from "../../format/formatValues.ts";
import type { DiffLine } from "../../notes/lineDiff.ts";
import type { NoteFileStats } from "../../notes/noteHistory.ts";
import type { RunView } from "../../state/useRunView.ts";
import { useViewState } from "../../state/ViewStateContext.tsx";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import { Button } from "../common/Button.tsx";
import { EmptyState } from "../common/EmptyState.tsx";
import { ErrorState } from "../common/ErrorState.tsx";
import { SegmentedControl } from "../common/SegmentedControl.tsx";
import { Skeleton } from "../common/Skeleton.tsx";
import content from "./DetailContent.module.css";
import styles from "./FileTab.module.css";

/** 表示の形の選択肢 */
const VIEW_OPTIONS = [
  { value: "diff", label: "差分" },
  { value: "full", label: "全文" },
] as const;

/** 差分の行の頭に付ける印 */
const LINE_PREFIX: Record<DiffLine["kind"], string> = { same: "  ", del: "- ", add: "+ " };

/** 差分を比べる先がいらない (全文の表示か、1 つ前の版が無い) ときの、1 つ前の版の状態 */
const NO_PREVIOUS: QueryView<PreviousNoteText> = { state: "ready", data: { kind: "none" } };

export type NoteFileViewerProps = {
  view: RunView;
  stats: readonly NoteFileStats[];
  kind: NoteKind;
  version: number;
};

/** ゲームの情報の 1 ファイルの表示部 */
export const NoteFileViewer = ({ view, stats, kind, version }: NoteFileViewerProps) => {
  const { state, dispatch } = useViewState();
  const noteVersions = view.summary.note_versions;
  const kindVersions = stats.find((stat) => stat.kind === kind)?.versions ?? [];
  const options = noteVersionOptions(noteVersions, kind, kindVersions);
  const previousVersion = previousNoteVersion(noteVersions, version);
  const comparesPrevious = state.fileView === "diff" && previousVersion !== null;
  const currentView = toQueryView(useNoteDetail(view.run, version));
  const previousQuery = toQueryView(
    useNoteDetail(view.run, comparesPrevious ? previousVersion : null),
  );
  const previousView = comparesPrevious ? previousTextView(previousQuery, kind) : NO_PREVIOUS;

  return (
    <>
      <p className={styles.filePath}>{noteFilePath(view.summary.game, kind)}</p>
      <div className={content.headRow}>
        <h5 className={content.subheading}>版</h5>
        <SegmentedControl
          label="表示の形"
          options={VIEW_OPTIONS}
          value={state.fileView}
          onChange={(value) => dispatch({ type: "setFileView", view: value })}
        />
      </div>
      <div className={content.buttonRow}>
        {options.map((option) => (
          <Button
            key={option.version}
            className={content.wrapButton}
            aria-pressed={option.version === version}
            onClick={() =>
              dispatch({
                type: "selectFile",
                file: { type: "note", kind, version: option.version },
              })
            }
          >
            {noteVersionLabel(option)}
          </Button>
        ))}
      </div>
      {currentView.state === "loading" || currentView.state === "idle" ? (
        <Skeleton height="8rem" />
      ) : null}
      {currentView.state === "error" ? (
        <ErrorState error={currentView.error} onRetry={currentView.retry} />
      ) : null}
      {currentView.state === "ready" && currentView.data.files === null ? (
        <EmptyState>この版はファイルに分けられない</EmptyState>
      ) : null}
      {currentView.state === "ready" && currentView.data.files !== null ? (
        <NoteBody
          versionText={noteVersionText(noteVersions, version)}
          text={currentView.data.files[kind]}
          previousVersion={previousVersion}
          previousView={previousView}
          fileView={state.fileView}
        />
      ) : null}
    </>
  );
};

/** 1 つ前の版の読み込みの状態を、そのファイルの中身 (PreviousNoteText) の状態にする */
const previousTextView = (
  query: QueryView<{ files: Record<NoteKind, string> | null }>,
  kind: NoteKind,
): QueryView<PreviousNoteText> => {
  if (query.state !== "ready") {
    return query;
  }
  const files = query.data.files;
  return {
    state: "ready",
    data: files === null ? { kind: "unsplit" } : { kind: "text", text: files[kind] },
  };
};

type NoteBodyProps = {
  /** 版の文 (noteVersionText) */
  versionText: string;
  /** 選んだ版のそのファイルの中身 */
  text: string;
  previousVersion: number | null;
  previousView: QueryView<PreviousNoteText>;
  fileView: "diff" | "full";
};

/** 選んだ版の中身。差分の表示で 1 つ前の版を読み込み中なら骨組みを出す */
const NoteBody = ({
  versionText,
  text,
  previousVersion,
  previousView,
  fileView,
}: NoteBodyProps) => {
  if (previousView.state === "loading" || previousView.state === "idle") {
    return <Skeleton height="8rem" />;
  }
  if (previousView.state === "error") {
    return <ErrorState error={previousView.error} onRetry={previousView.retry} />;
  }
  const shown = noteContentView(text, previousView.data, fileView);
  const facts = [versionText, `${countCharacters(text).toLocaleString("ja-JP")} 字`];
  if (shown.mode === "diff") {
    facts.push(`版 ${previousVersion} との差分 +${shown.added} −${shown.removed}`);
  } else if (shown.notice !== null) {
    facts.push(shown.notice);
  }
  return (
    <>
      <p className={content.note}>{facts.join(" · ")}</p>
      <div className={content.textBox}>
        <pre className={content.pre}>
          {shown.mode === "full"
            ? text
            : shown.lines.map((line, position) => (
                <span
                  key={diffLineKey(line, position)}
                  className={joinClassNames(styles.line, styles[line.kind])}
                >
                  {LINE_PREFIX[line.kind]}
                  {line.text}
                </span>
              ))}
        </pre>
      </div>
    </>
  );
};

/** 差分の行のキー (行の位置と種類。差分の行は並べ替えない) */
const diffLineKey = (line: DiffLine, position: number): string => `${position}:${line.kind}`;
