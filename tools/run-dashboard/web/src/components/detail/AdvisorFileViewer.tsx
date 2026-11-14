// ファイルのタブの表示部の、advisor の入出力の 1 ファイル。画像は画像として、それ以外は読んだテキストを pre で出す
// (HTML として解釈しない)。
import { filePath } from "../../api/apiPaths.ts";
import { toQueryView } from "../../api/queryView.ts";
import { fileNameOf, isImagePath } from "../../detail/requestFacts.ts";
import type { RunView } from "../../state/useRunView.ts";
import { EmptyState } from "../common/EmptyState.tsx";
import { ErrorState } from "../common/ErrorState.tsx";
import { Skeleton } from "../common/Skeleton.tsx";
import content from "./DetailContent.module.css";
import { DetailImage } from "./DetailImage.tsx";
import styles from "./FileTab.module.css";
import { useFileText } from "./useFileText.ts";

export type AdvisorFileViewerProps = {
  view: RunView;
  /** files の API のパス (advisor の実行の詳細の files[].path) */
  path: string;
};

/** advisor の入出力の 1 ファイルの表示部 */
export const AdvisorFileViewer = ({ view, path }: AdvisorFileViewerProps) => {
  const image = isImagePath(path);
  const textView = toQueryView(useFileText(view.run, image ? null : path));
  const src = filePath(view.run, path);
  return (
    <>
      <p className={styles.filePath}>{path}</p>
      {image ? <DetailImage key={src} src={src} alt={`${fileNameOf(path)} の画像`} /> : null}
      {textView.state === "loading" ? <Skeleton height="8rem" /> : null}
      {textView.state === "error" ? (
        <ErrorState error={textView.error} onRetry={textView.retry} />
      ) : null}
      {textView.state === "ready" && textView.data === "" ? (
        <EmptyState>空のファイル</EmptyState>
      ) : null}
      {textView.state === "ready" && textView.data !== "" ? (
        <div className={content.textBox}>
          <pre className={content.pre}>{textView.data}</pre>
        </div>
      ) : null}
    </>
  );
};
