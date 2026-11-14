// 詳細ペインの区画。いまは枠と見出しだけを出す。
import { Panel } from "../common/Panel.tsx";

export type DetailPaneProps = {
  /** 枠に足す class (画面の配置で決める大きさ) */
  className?: string;
};

/** 「詳細」の見出しの補足の区画 (aside) */
export const DetailPane = ({ className }: DetailPaneProps) => (
  <Panel as="aside" title="詳細" className={className} />
);
