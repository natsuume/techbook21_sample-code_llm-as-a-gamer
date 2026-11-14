// 丸い枠の短い文言 (状態や結果の表示)。onClick を渡すと押せる chip (button) になる。
import type { ReactNode } from "react";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import styles from "./Chip.module.css";

/**
 * chip の見た目。default は枠だけ、live は accent の地、running は実行中 (hot の破線の枠)、
 * external は受け持ちの advisor 以外 (danger の枠と文字)。色だけに頼らないよう、文言でも状態を示す
 */
export type ChipTone = "default" | "live" | "running" | "external";

export type ChipProps = {
  /** 既定は default */
  tone?: ChipTone;
  /** 渡すと button (type="button"、最小の高さ 1.875rem) にし、押したときに呼ぶ。無ければ span */
  onClick?: () => void;
  /** カーソルを合わせたときの補足 */
  title?: string;
  /** 文言だけでは押した結果が伝わらないときの、支援技術向けの名前 */
  "aria-label"?: string;
  className?: string;
  children: ReactNode;
};

/** 状態や結果を示す chip。押せる chip は button、押せない chip は span */
export const Chip = ({
  tone = "default",
  onClick,
  title,
  "aria-label": ariaLabel,
  className,
  children,
}: ChipProps) => {
  const chipClassName = joinClassNames(styles.chip, styles[tone], className);
  if (onClick === undefined) {
    return (
      <span className={chipClassName} title={title}>
        {children}
      </span>
    );
  }
  return (
    <button
      type="button"
      className={chipClassName}
      title={title}
      aria-label={ariaLabel}
      onClick={onClick}
    >
      {children}
    </button>
  );
};
