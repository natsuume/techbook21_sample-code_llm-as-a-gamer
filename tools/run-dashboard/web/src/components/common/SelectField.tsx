// 名前の付いた select (実行の選択・表示する幅の選択など)。名前は画面に出すか、aria-label にする。
import type { ReactNode, SelectHTMLAttributes } from "react";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import styles from "./SelectField.module.css";

/** 名前の出し方。visible は select の前に文字で出す (label 要素)、hidden は aria-label にする */
export type SelectLabelDisplay = "visible" | "hidden";

/** SelectField の props。select 要素の属性 (value・onChange など) はそのまま渡る */
export type SelectFieldProps = Omit<SelectHTMLAttributes<HTMLSelectElement>, "aria-label"> & {
  /** select の名前 */
  label: string;
  /** 既定は visible */
  labelDisplay?: SelectLabelDisplay;
  /** option 要素 */
  children: ReactNode;
};

/** 名前の付いた select。最小の高さは 2.125rem */
export const SelectField = ({
  label,
  labelDisplay = "visible",
  className,
  children,
  ...rest
}: SelectFieldProps) => {
  const selectClassName = joinClassNames(styles.select, className);
  if (labelDisplay === "hidden") {
    return (
      <select {...rest} aria-label={label} className={selectClassName}>
        {children}
      </select>
    );
  }
  return (
    <label className={styles.field}>
      {label}
      <select {...rest} className={selectClassName}>
        {children}
      </select>
    </label>
  );
};
