// データを読めなかったときの表示。読めなかった理由と「読み直す」ボタンを出す。
import { describeError } from "../../api/fetchJson.ts";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import { Button } from "./Button.tsx";
import styles from "./ErrorState.module.css";

export type ErrorStateProps = {
  /** 読めなかった理由 (query の error)。Error なら message、それ以外は文字列にして出す */
  error: unknown;
  /** 「読み直す」を押したときに呼ぶ (query の refetch など) */
  onRetry: () => void;
  className?: string;
};

/** 「読み込めなかった」の文言・理由・「読み直す」ボタンを 1 行 (幅が足りなければ折り返す) に並べる */
export const ErrorState = ({ error, onRetry, className }: ErrorStateProps) => (
  <div className={joinClassNames(styles.error, className)}>
    <p className={styles.message}>
      <span className={styles.title}>読み込めなかった</span>{" "}
      <span className={styles.reason}>{describeError(error)}</span>
    </p>
    <Button onClick={onRetry}>読み直す</Button>
  </div>
);
