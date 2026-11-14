// 確率の行の一覧。行ごとに、問いの名前・値の棒 (しきい値の縦線を出せる)・値・注記を横に並べる。
import type { ReactNode } from "react";
import { formatProbability, ratioToPercent } from "../../format/formatProbability.ts";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import styles from "./ProbabilityRows.module.css";
import { VisuallyHidden } from "./VisuallyHidden.tsx";

/**
 * 行の注記。tone の adopted は採った値 (warn の太字)、muted は控えめな表示。
 * reason は注記の理由 (例: 「不採用」の理由)。title と、画面に出さない文で示す
 */
export type ProbabilityNote = { text: string; tone: "adopted" | "muted"; reason?: string };

/** 1 行。値は 0〜1 (null は値なしで「—」と空の棒) */
export type ProbabilityRow = {
  /** 行を区別するキー (React の key) */
  key: string;
  /** 問いの名前 (等幅の文字で出す) */
  name: ReactNode;
  value: number | null;
  /** しきい値。渡すと棒に縦線を出し、値がしきい値を超えた棒を hot にする */
  threshold?: number | null;
  note?: ProbabilityNote | null;
  /** true の行を accent の枠で強調する (focusQuestion の問い) */
  focused?: boolean;
};

export type ProbabilityRowsProps = {
  rows: readonly ProbabilityRow[];
  /** 一覧の名前 (ol の aria-label) */
  label?: string;
  className?: string;
};

/** 確率の行を ol で並べる。列 (名前・棒・値・注記) の幅は全行でそろう */
export const ProbabilityRows = ({ rows, label, className }: ProbabilityRowsProps) => (
  <ol aria-label={label} className={joinClassNames(styles.rows, className)}>
    {rows.map((row) => {
      const threshold = row.threshold ?? null;
      const over = row.value !== null && threshold !== null && row.value > threshold;
      return (
        <li key={row.key} className={joinClassNames(styles.row, row.focused && styles.focused)}>
          <span className={styles.name}>{row.name}</span>
          <span className={styles.bar} aria-hidden="true">
            <span
              className={joinClassNames(styles.fill, over && styles.over)}
              style={{ width: ratioToPercent(row.value ?? 0) }}
            />
            {threshold === null ? null : (
              <span className={styles.threshold} style={{ left: ratioToPercent(threshold) }} />
            )}
          </span>
          <span className={styles.value}>{formatProbability(row.value)}</span>
          <span
            className={joinClassNames(styles.note, row.note?.tone === "adopted" && styles.adopted)}
            title={row.note?.reason}
          >
            {row.note?.text}
            {row.note?.reason === undefined ? null : (
              <VisuallyHidden> ({row.note.reason})</VisuallyHidden>
            )}
          </span>
        </li>
      );
    })}
  </ol>
);
