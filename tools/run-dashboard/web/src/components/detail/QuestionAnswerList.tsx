// 要求のタブの「問いの答え」の行の一覧。行ごとに、問いの名前・棒 (選択肢の問いは選択肢ごとの確率の積み上げ棒、
// 確率の問いは値の棒と advisor の問いのしきい値の線)・問いの値・採った答えを並べ、選択肢の問いは棒の下に確率の文を出す。
import type { RefObject } from "react";
import {
  type AnswerNote,
  answerNoteText,
  breakableQuestionName,
  type ChoiceSegment,
  describeChoiceSegments,
  type QuestionAnswerRow,
  REJECTED_REASON,
} from "../../detail/questionAnswers.ts";
import { formatProbability, ratioToPercent } from "../../format/formatProbability.ts";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import { VisuallyHidden } from "../common/VisuallyHidden.tsx";
import styles from "./QuestionAnswerList.module.css";

export type QuestionAnswerListProps = {
  rows: readonly QuestionAnswerRow[];
  /** 強調する行 (focused が true の行) の li を入れる ref (スクロールして見せるため) */
  focusedRowRef: RefObject<HTMLLIElement | null>;
  /** 一覧の名前 (ol の aria-label) */
  label: string;
};

/** 問いの答えの行を ol で並べる。列 (名前・棒・値・注記) の幅は全行でそろう */
export const QuestionAnswerList = ({ rows, focusedRowRef, label }: QuestionAnswerListProps) => (
  <ol aria-label={label} className={styles.rows}>
    {rows.map((row) => (
      <li
        key={row.name}
        ref={row.focused ? focusedRowRef : undefined}
        className={joinClassNames(styles.row, row.focused && styles.focused)}
        aria-current={row.focused ? "true" : undefined}
      >
        <span className={styles.name}>{breakableQuestionName(row.name)}</span>
        {row.segments === null ? (
          <ValueBar value={row.value} threshold={row.threshold} />
        ) : (
          <ChoiceBar segments={row.segments} />
        )}
        <span className={styles.value}>{formatProbability(row.value)}</span>
        <AnswerNoteText note={row.note} />
        {row.segments === null ? null : <ChoiceBreakdown segments={row.segments} />}
      </li>
    ))}
  </ol>
);

/** 確率の問いの棒。しきい値があれば縦線を出し、値がしきい値を超えた棒を hot にする */
const ValueBar = ({ value, threshold }: { value: number; threshold: number | null }) => (
  <span className={styles.bar} aria-hidden="true">
    <span
      className={joinClassNames(
        styles.fill,
        threshold !== null && value > threshold && styles.over,
      )}
      style={{ width: ratioToPercent(value) }}
    />
    {threshold === null ? null : (
      <span className={styles.threshold} style={{ left: ratioToPercent(threshold) }} />
    )}
  </span>
);

/** 選択肢の問いの積み上げ棒。none の区切りは地のまま (色を塗らない) */
const ChoiceBar = ({ segments }: { segments: readonly ChoiceSegment[] }) => (
  <span className={styles.bar} aria-hidden="true" title={describeChoiceSegments(segments)}>
    {segments
      .filter((segment) => segment.tone !== "none" && segment.width > 0)
      .map((segment) => (
        <span
          key={segment.name}
          className={joinClassNames(
            styles.segment,
            styles[segment.tone],
            segment.adopted && styles.adoptedSegment,
          )}
          style={{ left: ratioToPercent(segment.start), width: ratioToPercent(segment.width) }}
        />
      ))}
  </span>
);

/** 採った答えの注記。不採用は理由を title と画面に出さない文で示す */
const AnswerNoteText = ({ note }: { note: AnswerNote | null }) => {
  if (note === null) {
    return <span className={styles.note} />;
  }
  const rejected = note.kind === "rejected";
  return (
    <span
      className={joinClassNames(styles.note, !rejected && styles.adopted)}
      title={rejected ? REJECTED_REASON : undefined}
    >
      {answerNoteText(note)}
      {rejected ? <VisuallyHidden> ({REJECTED_REASON})</VisuallyHidden> : null}
    </span>
  );
};

/** 選択肢ごとの確率の文。採った選択肢は太字にし、画面に出さない文で「採用」を添える */
const ChoiceBreakdown = ({ segments }: { segments: readonly ChoiceSegment[] }) => (
  <span className={styles.breakdown}>
    {segments.map((segment, position) => (
      <span key={segment.name}>
        {position === 0 ? null : " · "}
        <span className={segment.adopted ? styles.adoptedChoice : undefined}>
          {segment.name} {formatProbability(segment.value)}
          {segment.adopted ? <VisuallyHidden> (採用)</VisuallyHidden> : null}
        </span>
      </span>
    ))}
  </span>
);
