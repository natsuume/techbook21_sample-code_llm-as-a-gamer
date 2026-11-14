// 文字のボタン (と、IconButton が使う土台)。押せないときは native の disabled ではなく aria-disabled にし、
// フォーカスできるまま押せない理由を aria-describedby と title で示す。
import { type ButtonHTMLAttributes, type ReactNode, useId } from "react";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import styles from "./Button.module.css";
import { VisuallyHidden } from "./VisuallyHidden.tsx";

/** ボタンの見た目。default は surface-2 の地と枠、primary は accent の地、ghost は地と枠なし */
export type ButtonVariant = "default" | "primary" | "ghost";

/** ボタンの形。text は文字の幅に合わせた形、square は 2.125rem 四方 (アイコンだけのボタン) */
export type ButtonShape = "text" | "square";

/** Button の props。button 要素の属性 (onClick・aria-pressed・aria-expanded・aria-controls など) はそのまま渡る */
export type ButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type" | "disabled"> & {
  /** 既定は default */
  variant?: ButtonVariant;
  /** 既定は text */
  shape?: ButtonShape;
  /**
   * 押せない理由。null 以外を渡すと aria-disabled="true" にして onClick を呼ばず、理由を aria-describedby
   * (画面に出さない文) と title (カーソルを合わせたときの表示) で示す。押せるときは null か省略
   */
  disabledReason?: string | null;
  children: ReactNode;
};

/** type="button" のボタン。aria-pressed="true" で選んでいる見た目になる */
export const Button = ({
  variant = "default",
  shape = "text",
  disabledReason = null,
  className,
  onClick,
  title,
  children,
  "aria-describedby": describedBy,
  ...rest
}: ButtonProps) => {
  const reasonId = useId();
  const unavailable = disabledReason !== null;
  return (
    <>
      <button
        type="button"
        {...rest}
        className={joinClassNames(styles.button, styles[variant], styles[shape], className)}
        aria-disabled={unavailable ? true : undefined}
        aria-describedby={
          unavailable
            ? [describedBy, reasonId].filter((id) => id !== undefined).join(" ")
            : describedBy
        }
        title={unavailable ? disabledReason : title}
        onClick={unavailable ? undefined : onClick}
      >
        {children}
      </button>
      {unavailable ? <VisuallyHidden id={reasonId}>{disabledReason}</VisuallyHidden> : null}
    </>
  );
};
