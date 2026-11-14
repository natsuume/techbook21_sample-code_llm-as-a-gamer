// タイムラインの下の、カーソルを合わせた帯・ヒートマップ・折れ線・入力の値を出す 1 行 (aria-live="polite")。
// 文はこの部品だけが持ち、カーソルを動かしてもタイムラインのほかの部分を描き直さない。
import { type Ref, useImperativeHandle, useState } from "react";
import { DEFAULT_READOUT } from "../../timeline/timelineTexts.ts";
import styles from "./HoverReadout.module.css";

/** 値の 1 行を変える操作。show(null) でカーソルを合わせる前の文に戻す */
export type HoverReadoutHandle = { show: (text: string | null) => void };

export type HoverReadoutProps = {
  ref?: Ref<HoverReadoutHandle>;
};

/** 1 行の高さを予約した文。長い文は末尾を省略し、全文は title で読める */
export const HoverReadout = ({ ref }: HoverReadoutProps) => {
  const [text, setText] = useState<string | null>(null);
  useImperativeHandle(ref, () => ({ show: setText }), []);
  const shown = text ?? DEFAULT_READOUT;
  return (
    <p className={styles.readout} aria-live="polite" title={shown}>
      {shown}
    </p>
  );
};
