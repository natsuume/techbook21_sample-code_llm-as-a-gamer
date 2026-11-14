// 詳細ペインの画像 (縮小画像・advisor の入出力の画像)。16:9 の枠で大きさを予約し、画像が無いか読めなければ文言を出す。
import { useState } from "react";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import styles from "./DetailImage.module.css";

export type DetailImageProps = {
  /** 画像の URL。null なら画像の代わりに emptyText を出す */
  src: string | null;
  /** 画像の代替の文。飾りの画像 (横に同じ内容の文がある) は "" */
  alt: string;
  /** src が null のときの文言 (既定は「画像なし」) */
  emptyText?: string;
  /** 枠に足す class */
  className?: string;
};

/** 画像の枠と画像。読み込みに失敗したら「画像なし」に替える (src が変わったら呼び出し側の key で作り直す) */
export const DetailImage = ({ src, alt, emptyText = "画像なし", className }: DetailImageProps) => {
  const [failed, setFailed] = useState(false);
  return (
    <span className={joinClassNames(styles.frame, className)}>
      {src === null || failed ? (
        <span className={styles.missing}>{src === null ? emptyText : "画像なし"}</span>
      ) : (
        <img className={styles.image} src={src} alt={alt} onError={() => setFailed(true)} />
      )}
    </span>
  );
};
