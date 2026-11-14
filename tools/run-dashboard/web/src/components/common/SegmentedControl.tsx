// 少数の選択肢から 1 つを選ぶ、並んだボタンの組。選んでいる選択肢は aria-pressed="true" で示す。
// 押せない選択肢は native の disabled ではなく aria-disabled にし、フォーカスできるまま理由を aria-describedby と
// title で示す (Button と同じ)。
import { Fragment, type ReactNode, useId } from "react";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import styles from "./SegmentedControl.module.css";
import { VisuallyHidden } from "./VisuallyHidden.tsx";

/**
 * 選択肢。value は選んだときに onChange に渡す値、label はボタンの文言。disabledReason は押せない理由
 * (null 以外なら押しても onChange を呼ばない。押せるときは null か省略)
 */
export type SegmentedOption<T extends string | number> = {
  value: T;
  label: ReactNode;
  disabledReason?: string | null;
};

export type SegmentedControlProps<T extends string | number> = {
  /** 組の名前 (fieldset の aria-label) */
  label: string;
  options: readonly SegmentedOption<T>[];
  /** 選んでいる値 */
  value: T;
  /** 選択肢を押したときに、その値で呼ぶ */
  onChange: (value: T) => void;
  className?: string;
};

/** 選択肢のボタンを横に並べた組 (fieldset) */
export const SegmentedControl = <T extends string | number>({
  label,
  options,
  value,
  onChange,
  className,
}: SegmentedControlProps<T>) => {
  const reasonIdPrefix = useId();
  return (
    <fieldset aria-label={label} className={joinClassNames(styles.group, className)}>
      {options.map((option, index) => {
        const reason = option.disabledReason ?? null;
        const reasonId = `${reasonIdPrefix}-${index}`;
        return (
          <Fragment key={String(option.value)}>
            <button
              type="button"
              className={styles.option}
              aria-pressed={option.value === value}
              aria-disabled={reason === null ? undefined : true}
              aria-describedby={reason === null ? undefined : reasonId}
              title={reason ?? undefined}
              onClick={reason === null ? () => onChange(option.value) : undefined}
            >
              {option.label}
            </button>
            {reason === null ? null : <VisuallyHidden id={reasonId}>{reason}</VisuallyHidden>}
          </Fragment>
        );
      })}
    </fieldset>
  );
};
