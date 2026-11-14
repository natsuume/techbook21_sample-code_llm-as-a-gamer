// 少数の選択肢から 1 つを選ぶ、並んだボタンの組。選んでいる選択肢は aria-pressed="true" で示す。
import type { ReactNode } from "react";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import styles from "./SegmentedControl.module.css";

/** 選択肢。value は選んだときに onChange に渡す値、label はボタンの文言 */
export type SegmentedOption<T extends string | number> = { value: T; label: ReactNode };

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
}: SegmentedControlProps<T>) => (
  <fieldset aria-label={label} className={joinClassNames(styles.group, className)}>
    {options.map((option) => (
      <button
        key={String(option.value)}
        type="button"
        className={styles.option}
        aria-pressed={option.value === value}
        onClick={() => onChange(option.value)}
      >
        {option.label}
      </button>
    ))}
  </fieldset>
);
