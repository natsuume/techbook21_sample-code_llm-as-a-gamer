// タイムラインの区画。いまは枠と見出しだけを出す。
import { Panel } from "../common/Panel.tsx";

export type TimelineProps = {
  /** 枠に足す class (画面の配置で決める大きさとスクロール) */
  className?: string;
};

/** 「タイムライン」の見出しの区画 */
export const Timeline = ({ className }: TimelineProps) => (
  <Panel title="タイムライン" className={className} />
);
