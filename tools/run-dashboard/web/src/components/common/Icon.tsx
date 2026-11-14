// 24×24 の線で描いたアイコン。文字の色 (currentColor) で描き、支援技術には読ませない (aria-hidden)。
import styles from "./Icon.module.css";

/** アイコンの種類。prev・next は前後への移動、expand・collapse は展開と畳む、detail は詳細を開く、pause は一時停止 */
export type IconName = "prev" | "next" | "expand" | "collapse" | "detail" | "pause";

/** アイコンの大きさ。small は 0.875rem、medium は 1rem 四方 */
export type IconSize = "small" | "medium";

export type IconProps = {
  name: IconName;
  /** 既定は medium */
  size?: IconSize;
};

/** アイコンごとの path の d 属性 (viewBox 0 0 24 24) */
const ICON_PATHS: Record<IconName, readonly string[]> = {
  prev: ["M15 6l-6 6 6 6"],
  next: ["M9 6l6 6-6 6"],
  expand: ["M6 9l6 6 6-6"],
  collapse: ["M6 15l6-6 6 6"],
  detail: ["M9 6l6 6-6 6"],
  pause: ["M9 5v14", "M15 5v14"],
};

/** 線のアイコンの svg。ボタンの中で文字の横に置くか、IconButton の中身にする */
export const Icon = ({ name, size = "medium" }: IconProps) => (
  <svg
    aria-hidden="true"
    className={styles[size]}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={2}
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    {ICON_PATHS[name].map((d) => (
      <path key={d} d={d} />
    ))}
  </svg>
);
