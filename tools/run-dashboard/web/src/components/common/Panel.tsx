// 区画の枠 (地 surface、1px の line の枠、角丸、余白)。見出しの行 (見出し・補足・操作) と本文を縦に並べる。
import { type ReactNode, useId } from "react";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import styles from "./Panel.module.css";

/** 区画の要素。section は画面の区画、aside は補足の区画 (詳細ペイン) */
export type PanelElement = "section" | "aside";

export type PanelProps = {
  /** 見出し (h2 の中身)。区画の名前にもなる (aria-labelledby)。無ければ見出しの行を出さない */
  title?: ReactNode;
  /** 見出しの横に並べる補足 (chip・短い文など。muted の小さい文字) */
  meta?: ReactNode;
  /** 見出しの行の右端に寄せる操作 (ボタン・select など) */
  actions?: ReactNode;
  /** title が無いときの区画の名前 (aria-label) */
  label?: string;
  /** 既定は section */
  as?: PanelElement;
  /** 読み込み中 (骨組みを出している間) は true にする (aria-busy) */
  busy?: boolean;
  /** 枠に足す class (配置の大きさ・スクロールなど、呼び出し側の配置で決めるもの) */
  className?: string;
  /** 本文。枠の中に、見出しの行の下へ縦に並べる (間は 0.5rem) */
  children?: ReactNode;
};

/** 見出しの行と本文を持つ区画の枠 */
export const Panel = ({
  title,
  meta,
  actions,
  label,
  as: Element = "section",
  busy,
  className,
  children,
}: PanelProps) => {
  const titleId = useId();
  const hasTitle = title !== undefined && title !== null;
  const hasHead = hasTitle || meta !== undefined || actions !== undefined;
  return (
    <Element
      className={joinClassNames(styles.panel, className)}
      aria-labelledby={hasTitle ? titleId : undefined}
      aria-label={hasTitle ? undefined : label}
      aria-busy={busy ? true : undefined}
    >
      {hasHead ? (
        <div className={styles.head}>
          {hasTitle ? (
            <h2 id={titleId} className={styles.title}>
              {title}
            </h2>
          ) : null}
          {meta === undefined ? null : <div className={styles.meta}>{meta}</div>}
          {actions === undefined ? null : <div className={styles.actions}>{actions}</div>}
        </div>
      ) : null}
      {children}
    </Element>
  );
};
